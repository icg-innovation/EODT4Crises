from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests
import json
import glob
import os
import sys
import time
import subprocess
import shutil
import pickle
import rasterio
from rasterio.transform import Affine

from torch.cuda import is_available

from pyproj import Transformer
import logging

from PIL import Image, ImageDraw
from shapely.geometry import shape
from shapely.ops import unary_union

import geopandas as gpd

from image_providers.provider_factory import get_provider
from utils.image_processing import process_geotiff_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

CURRENT_DIR = os.path.dirname(__file__)
SAM_ROAD_CONFIG_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "model_files", "spacenet_custom.yaml"))
SAM_ROAD_CHECKPOINT_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "model_files", "spacenet_vitb_256_e10.ckpt"))
SAM_ROAD_PROJECT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "data_processing"))
backend_static_folder = os.path.abspath(os.path.join(CURRENT_DIR, "static"))

print("--- Cleaning up old generated files ---")
if os.path.exists(backend_static_folder):
    files_to_delete = glob.glob(os.path.join(backend_static_folder, "*.png"))
    files_to_delete += glob.glob(os.path.join(backend_static_folder, "*.tif"))
    for f_path in files_to_delete:
        try:
            os.remove(f_path)
            print(f"Deleted: {os.path.basename(f_path)}")
        except OSError as e:
            print(f"Error deleting file {f_path}: {e}")
else:
    os.makedirs(backend_static_folder)

frontend_static_folder = os.path.abspath(os.path.join(CURRENT_DIR, "..", "frontend", "public"))
app = Flask(__name__, static_folder=frontend_static_folder, static_url_path="")
app.config["TEMPLATES_AUTO_RELOAD"] = True
logging.info(f"Serving static files from frontend: {frontend_static_folder}")

@app.route("/static/<path:filename>")
def backend_static(filename):
    return send_from_directory(backend_static_folder, filename)

CORS(app, resources={r"/api/*": {"origins": "*"}})

def overpass_to_geojson(overpass_json):
    nodes = {}
    for element in overpass_json.get("elements", []):
        if element["type"] == "node":
            nodes[element["id"]] = [element["lon"], element["lat"]]
    features = []
    for element in overpass_json.get("elements", []):
        if element["type"] == "way":
            geometry = {
                "type": "LineString",
                "coordinates": [
                    nodes.get(node_id)
                    for node_id in element.get("nodes", [])
                    if nodes.get(node_id)
                ],
            }
            if all(geometry["coordinates"]):
                features.append(
                    {
                        "type": "Feature",
                        "properties": element.get("tags", {}),
                        "geometry": geometry,
                    }
                )
    return {"type": "FeatureCollection", "features": features}

def create_osm_mask(geojson_data, image_bounds, image_size=(512, 512), line_width=3):
    height, width = image_size
    lat_min, lat_max, lon_min, lon_max = image_bounds
    mask_image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask_image)
    def geo_to_pixel(lon, lat):
        if lon_max == lon_min or lat_max == lat_min:
            return 0, 0
        x = (lon - lon_min) * (width / (lon_max - lon_min))
        y = (lat_max - lat) * (height / (lat_max - lat_min))
        return int(x), int(y)
    for feature in geojson_data.get("features", []):
        if feature["geometry"]["type"] == "LineString":
            coordinates = feature["geometry"]["coordinates"]
            pixel_points = [geo_to_pixel(lon, lat) for lon, lat in coordinates]
            if len(pixel_points) >= 2:
                draw.line(pixel_points, fill=255, width=line_width)
    return mask_image

def graph_to_geojson(adjacency_list, transform, crs):
    features = []
    try:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        for source_node_yx, dest_nodes_yx in adjacency_list.items():
            for dest_node_yx in dest_nodes_yx:
                source_y_pixel, source_x_pixel = source_node_yx
                dest_y_pixel, dest_x_pixel = dest_node_yx
                
                start_x_proj, start_y_proj = (source_x_pixel + 0.5, source_y_pixel + 0.5) * transform
                end_x_proj, end_y_proj = (dest_x_pixel + 0.5, dest_y_pixel + 0.5) * transform

                start_lon, start_lat = transformer.transform(start_x_proj, start_y_proj)
                end_lon, end_lat = transformer.transform(end_x_proj, end_y_proj)

                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [float(start_lon), float(start_lat)],
                            [float(end_lon), float(end_lat)],
                        ],
                    },
                    "properties": {},
                }
                features.append(feature)
    except Exception as e:
        logging.error(f"Error during graph to GeoJSON conversion: {e}")
        return {"type": "FeatureCollection", "features": []}
    return {"type": "FeatureCollection", "features": features}

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/api/get_roads", methods=["GET"])
def get_roads():
    bbox = request.args.get("bbox")
    types_str = request.args.get("types")
    query_date = request.args.get("date")

    logging.info(f"Received request for OSM roads with bbox: {bbox} for date: {query_date}")
    
    if not bbox:
        return jsonify({"error": "Missing 'bbox' query parameter"}), 400
    if not types_str:
        return jsonify({"type": "FeatureCollection", "features": []})

    try:
        min_lon, min_lat, max_lon, max_lat = [float(coord) for coord in bbox.split(",")]
        overpass_bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    except (ValueError, IndexError) as e:
        logging.error(f"Invalid 'bbox' format: {bbox}. Error: {e}")
        return jsonify({"error": "Invalid 'bbox' format."}), 400

    overpass_types = "|".join(types_str.split(","))
    overpass_url = "https://overpass-api.de/api/interpreter"
    date_setting = f'[date:"{query_date}T23:59:59Z"]' if query_date else ""

    overpass_query = f"""
        [out:json][timeout:25]{date_setting};
        (way["highway"~"^({overpass_types})$"]({overpass_bbox}););
        out body;>;out skel qt;
    """
    try:
        response = requests.post(overpass_url, data={"data": overpass_query})
        logging.info(f"Overpass API response status: {response.status_code}")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch data from Overpass API: {e}"}), 502
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse response from Overpass API."}), 500

    return jsonify(overpass_to_geojson(data))

@app.route("/api/upload_image", methods=["POST"])
def upload_image():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    
    file = request.files['file']
    prefix = request.form.get('prefix', 'temp')

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if file and file.filename.lower().endswith(('.tif', '.tiff')):
        raw_tiff_filename = f"temp_satellite_{prefix}.tif"
        save_path = os.path.join(backend_static_folder, raw_tiff_filename)
        
        try:
            file.save(save_path)
            logging.info(f"Uploaded file saved to: {save_path}")
            raw_tiff_url = f"/static/{raw_tiff_filename}"
            return jsonify({
                "message": "Upload successful",
                "imageDate": "N/A (Local Upload)",
                "prefix": prefix,
                "rawTiffUrl": raw_tiff_url
            })
        except Exception as e:
            logging.error(f"Failed to save uploaded file: {e}")
            return jsonify({"error": "Failed to save file on server."}), 500
    
    return jsonify({"error": "Invalid file type. Please upload a GeoTIFF (.tif, .tiff)."}), 400

@app.route("/api/download_satellite_image", methods=["POST"])
def download_satellite_image():
    data = request.get_json()
    
    bbox = data.get("bbox")
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    target_date = data.get("target_date")
    prefix = data.get("prefix", "temp")
    source_provider = data.get("source_provider")
    credentials = data.get("credentials", {})
    options = data.get("options", {})

    if not all([bbox, start_date, end_date, target_date, source_provider]):
        return jsonify({"error": "Missing required parameters"}), 400

    try:
        lon_st, lat_st, lon_ed, lat_ed = [float(coord) for coord in bbox.split(",")]
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid 'bbox' format."}), 400

    geotiff_filename = f"temp_satellite_{prefix}.tif"
    geotiff_path = os.path.join(backend_static_folder, geotiff_filename)

    try:
        provider = get_provider(source_provider, credentials)
        _, image_date, stac_bbox = provider.download_image(
            lat_st=lat_st, lon_st=lon_st, lat_ed=lat_ed, lon_ed=lon_ed,
            start_date=start_date, end_date=end_date, target_date=target_date,
            output_path=geotiff_path,
            scale=5,
            options=options
        )
        raw_tiff_url = f"/static/{geotiff_filename}"
        return jsonify({
            "message": "Download successful",
            "imageDate": image_date,
            "prefix": prefix,
            "stac_bbox": stac_bbox,
            "rawTiffUrl": raw_tiff_url
        })

    except Exception as e:
        logging.error(f"Image download failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/api/process_satellite_image", methods=["GET"])
def process_satellite_image():
    satellite = request.args.get("satellite")
    prefix = request.args.get("prefix")
    stac_bbox_str = request.args.get("stac_bbox")

    if not prefix:
        return jsonify({"error": "Missing prefix"}), 400

    geotiff_path = os.path.join(backend_static_folder, f"temp_satellite_{prefix}.tif")
    if not os.path.exists(geotiff_path):
        return jsonify({"error": "Satellite data not found"}), 404

    try:
        unique_id = f"{prefix}_{int(time.time())}"
        png_filename = f"satellite_image_{unique_id}.png"
        png_path = os.path.join(backend_static_folder, png_filename)
        
        leaflet_bounds = None
        raw_bounds = None

        disable_norm = request.args.get('disable_normalization', 'false').lower() in ['1', 'true', 'yes']
        if stac_bbox_str:
            lon_min, lat_min, lon_max, lat_max = [float(b) for b in stac_bbox_str.split(',')]
            leaflet_bounds = [[lat_min, lon_min], [lat_max, lon_max]]
            raw_bounds = {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max}
            process_geotiff_image(tif_path=geotiff_path, save_path=png_path, satellite=satellite, normalize=not disable_norm)
        else:
            bounds_4326 = process_geotiff_image(tif_path=geotiff_path, save_path=png_path, satellite=satellite, normalize=not disable_norm)
            if bounds_4326:
                lat_min, lat_max, lon_min, lon_max = bounds_4326
                leaflet_bounds = [[lat_min, lon_min], [lat_max, lon_max]]
                raw_bounds = {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max}

        if not leaflet_bounds:
            return jsonify({"error": "Could not determine image bounds."}), 500

        return jsonify({
            "imageUrl": f"/static/{png_filename}",
            "bounds": leaflet_bounds,
            "rawBounds": raw_bounds
        })
    except Exception as e:
        logging.error(f"Image processing failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/api/generate_osm_mask", methods=["POST"])
def generate_osm_mask():
    try:
        request_data = request.get_json()
        osm_geojson = request_data.get('osm_data')
        image_bounds_str = request_data.get('image_bounds')

        if not all([osm_geojson, image_bounds_str]):
            return jsonify({"error": "Missing 'osm_data' or 'image_bounds' in request body"}), 400

        image_bounds = [float(b) for b in image_bounds_str.split(',')]
        osm_mask_image = create_osm_mask(osm_geojson, image_bounds, image_size=(512, 512))

        unique_id = int(time.time())
        mask_filename = f"osm_mask_{unique_id}.png"
        mask_path = os.path.join(backend_static_folder, mask_filename)
        osm_mask_image.save(mask_path)
        return jsonify({"maskUrl": f"/static/{mask_filename}"})

    except (ValueError, IndexError, TypeError) as e:
        return jsonify({"error": f"Invalid parameter format or error during mask creation: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/get_predicted_roads", methods=["GET"])
def get_predicted_roads():
    try:
        prefix = request.args.get("prefix", "pre")
        bbox_str = request.args.get("bbox")
        image_param = request.args.get("image")

        # Default path (from uploads or downloads) — kept for backward compatibility
        default_geotiff = os.path.join(backend_static_folder, f"temp_satellite_{prefix}.tif")

        # If the frontend passed a static image URL (e.g. '/static/case_studies/.../satellite_pre.tif'),
        # convert it to a filesystem path inside backend_static_folder and validate it exists.
        input_geotiff_path = default_geotiff
        if image_param:
            try:
                # Only accept paths that begin with '/static/' to avoid arbitrary file access
                if not image_param.startswith('/static/'):
                    raise ValueError('Only /static/ paths are accepted for the image parameter')
                # Map '/static/...' -> backend_static_folder/...
                rel_path = image_param[len('/static/'):]
                candidate_path = os.path.join(backend_static_folder, rel_path)
                # Normalize path and ensure it is inside backend_static_folder
                candidate_real = os.path.realpath(candidate_path)
                if not candidate_real.startswith(os.path.realpath(backend_static_folder)):
                    raise ValueError('Image path is outside allowed static directory')
                if os.path.exists(candidate_real):
                    input_geotiff_path = candidate_real
                else:
                    logging.warning(f"Requested case study image not found: {candidate_real}; falling back to default: {default_geotiff}")
            except Exception as e:
                logging.warning(f"Invalid image parameter provided: {e}; falling back to default geotiff.")
        # By default we'll process the original file, but if a bbox is provided and the
        # GeoTIFF has a valid CRS we will create a cropped temporary GeoTIFF and pass
        # that to the inferencer so the blue-box crop is actually applied.
        image_to_process = input_geotiff_path
        cropped_path = None
        if not os.path.exists(input_geotiff_path):
            return jsonify({"error": f"GeoTIFF not found: temp_satellite_{prefix}.tif"}), 404

    # If a bbox was provided try to crop the GeoTIFF to the geographic bbox
        leaflet_bounds = None
        if bbox_str and os.path.exists(input_geotiff_path):
            try:
                min_lon, min_lat, max_lon, max_lat = [float(c) for c in bbox_str.split(',')]
                leaflet_bounds = [[min_lat, min_lon], [max_lat, max_lon]]
                with rasterio.open(input_geotiff_path) as src:
                    if src.crs is None:
                        logging.warning("Input GeoTIFF has no CRS; cannot apply geographic bbox crop. Running inference on full image.")
                    else:
                        # Transform bbox to image CRS and build a window, then write a cropped GeoTIFF
                        left, bottom, right, top = rasterio.warp.transform_bounds('EPSG:4326', src.crs, min_lon, min_lat, max_lon, max_lat)
                        window = rasterio.windows.from_bounds(left, bottom, right, top, src.transform)
                        # Read the windowed data and write a smaller GeoTIFF to speed up inference
                        data = src.read(window=window)
                        window_transform = src.window_transform(window)
                        out_meta = src.meta.copy()
                        out_meta.update({
                            'height': data.shape[1],
                            'width': data.shape[2],
                            'transform': window_transform
                        })
                        cropped_filename = f"temp_satellite_{prefix}_crop.tif"
                        cropped_path = os.path.join(backend_static_folder, cropped_filename)
                        with rasterio.open(cropped_path, 'w', **out_meta) as dst:
                            dst.write(data)
                        image_to_process = cropped_path
            except Exception as e:
                logging.warning(f"Could not crop GeoTIFF to bbox; falling back to full image. Error: {e}")

                # If the frontend passed a case-study image, check the same directory for
                # saved prediction outputs (graph and mask). If present, return them directly
                # so we don't run the ML model.
                try:
                    case_dir = os.path.dirname(input_geotiff_path)
                    # Candidate graph/mask filenames commonly used in saved case outputs
                    candidate_graphs = [
                        os.path.join(case_dir, f"graph_{prefix}.p"),
                        os.path.join(case_dir, f"{prefix}.p"),
                        os.path.join(case_dir, "0.p"),
                        os.path.join(case_dir, "graph.p")
                    ]
                    candidate_masks = [
                        os.path.join(case_dir, f"predicted_mask_{prefix}.png"),
                        os.path.join(case_dir, f"predicted_mask.png"),
                        os.path.join(case_dir, "0_road.png"),
                        os.path.join(case_dir, "mask.png")
                    ]

                    found_graph = next((p for p in candidate_graphs if os.path.exists(p)), None)
                    found_mask = next((p for p in candidate_masks if os.path.exists(p)), None)

                    if found_graph and found_mask:
                        logging.info(f"Found saved case outputs, returning saved graph+mask from {case_dir}")
                        with open(found_graph, 'rb') as f:
                            predicted_graph_data = pickle.load(f)

                        # Determine CRS/transform from the image_to_process (cropped or original)
                        with rasterio.open(image_to_process) as src:
                            crs = src.crs
                            transform = src.transform

                        # If an accompanying transform JSON exists in the directory, prefer it
                        transform_json_path = None
                        for candidate in os.listdir(case_dir):
                            if candidate.endswith('_transform.json') or candidate.endswith('transform.json'):
                                transform_json_path = os.path.join(case_dir, candidate)
                                break
                        if transform_json_path and os.path.exists(transform_json_path):
                            try:
                                with open(transform_json_path, 'r') as f_t:
                                    transform = Affine.from_gdal(*json.load(f_t))
                            except Exception:
                                logging.warning('Could not load transform JSON; falling back to image transform')

                        predicted_roads_geojson = graph_to_geojson(predicted_graph_data, transform, crs)

                        unique_id = f"{prefix}_{int(time.time())}"
                        mask_filename = f"predicted_mask_{unique_id}.png"
                        shutil.copy(found_mask, os.path.join(backend_static_folder, mask_filename))

                        return jsonify({
                            "geojson": predicted_roads_geojson,
                            "maskUrl": f"/static/{mask_filename}",
                            "bounds": leaflet_bounds
                        })
                except Exception as e:
                    logging.warning(f"Error while checking for saved case outputs: {e}")

        output_dir_name = f"sentinel_test_{prefix}"
        model_output_dir = os.path.join(SAM_ROAD_PROJECT_DIR, "save", output_dir_name)
        python_executable = sys.executable
        inference_script_path = os.path.join(SAM_ROAD_PROJECT_DIR, "inferencer.py")
        torch_device = "cuda" if is_available() else "cpu"

        command = [
            python_executable, inference_script_path,
            "--config", SAM_ROAD_CONFIG_PATH,
            "--checkpoint", SAM_ROAD_CHECKPOINT_PATH,
            "--device", torch_device,
            "--output_dir", output_dir_name,
            "--images", image_to_process
        ]

        if bbox_str:
            # still pass bbox to inferencer (it will additionally crop if possible),
            # but we've already created a cropped GeoTIFF in image_to_process where applicable.
            command.extend(["--bbox"])
            command.extend(bbox_str.split(','))

        print(f"--- Executing inference command: {' '.join(command)} ---")

        try:
            subprocess.run(command, capture_output=True, text=True, check=True, cwd=SAM_ROAD_PROJECT_DIR)
        except subprocess.CalledProcessError as e:
            logging.error(f"Inference error: {e.stderr}")
            return jsonify({"error": "Failed to run road prediction model.", "details": e.stderr}), 500

        graph_path = os.path.join(model_output_dir, "graph", "0.p")
        mask_image_path = os.path.join(model_output_dir, "mask", "0_road.png")
        transform_path = os.path.join(model_output_dir, "graph", "0_transform.json") # Path to the new transform file

        if not all(os.path.exists(p) for p in [graph_path, mask_image_path]):
            return jsonify({"error": "Model output or georeference file not found."}), 500

        with open(graph_path, "rb") as f:
            predicted_graph_data = pickle.load(f)

        # Use the processed image (cropped if created) to determine CRS/transform
        with rasterio.open(image_to_process) as src:
            crs = src.crs
            transform = src.transform
            if os.path.exists(transform_path):
                with open(transform_path, 'r') as f_transform:
                    transform = Affine.from_gdal(*json.load(f_transform))
        
        predicted_roads_geojson = graph_to_geojson(predicted_graph_data, transform, crs)

        unique_id = f"{prefix}_{int(time.time())}"
        mask_filename = f"predicted_mask_{unique_id}.png"
        shutil.copy(mask_image_path, os.path.join(backend_static_folder, mask_filename))

        return jsonify({"geojson": predicted_roads_geojson,
                        "maskUrl": f"/static/{mask_filename}",
                        "bounds": leaflet_bounds})

    except Exception as e:
        logging.error(f"An unexpected error occurred in get_predicted_roads: {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred.", "details": str(e)}), 500

def get_prediction_geojson(prefix):
    """Helper function to load graph, transform, and convert to GeoJSON."""
    geotiff_path = os.path.join(backend_static_folder, f"temp_satellite_{prefix}.tif")
    model_output_dir = os.path.join(SAM_ROAD_PROJECT_DIR, "save", f"sentinel_test_{prefix}")
    graph_path = os.path.join(model_output_dir, "graph", "0.p")
    transform_path = os.path.join(model_output_dir, "graph", "0_transform.json")

    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Prediction file not found: {graph_path}")

    with open(graph_path, "rb") as f:
        graph_data = pickle.load(f)

    with rasterio.open(geotiff_path) as src:
        crs = src.crs
        transform = src.transform # Default transform
        if os.path.exists(transform_path):
            with open(transform_path, 'r') as f_transform:
                transform = Affine.from_gdal(*json.load(f_transform))

    return graph_to_geojson(graph_data, transform, crs)


@app.route("/api/compare_roads", methods=["POST"])
def compare_roads():
    try:
        request_data = request.get_json()
        osm_geojson = request_data.get('osm_data')
        if not osm_geojson:
            return jsonify({"error": "Missing 'osm_data' in request body"}), 400

        pre_event_geojson = get_prediction_geojson('pre')
        post_event_geojson = get_prediction_geojson('post')

    except FileNotFoundError as e:
        logging.error(f"Prediction file not found: {e}")
        return jsonify({"error": "A prediction file was not found. Please run both detections first."}), 404
    except Exception as e:
        logging.error(f"Error loading prediction data: {e}")
        return jsonify({"error": "Could not load prediction data."}), 500

    try:
        post_lines = [shape(feature["geometry"]) for feature in post_event_geojson["features"]]
        osm_lines = [shape(feature["geometry"]) for feature in osm_geojson["features"]]
        if not osm_lines:
            return jsonify({"error": "No OSM roads found in the data to use as a reference."}), 404

        # Use a small buffer to account for minor prediction inaccuracies
        post_union = unary_union(post_lines).buffer(0.0001) if post_lines else None
        osm_union = unary_union(osm_lines).buffer(0.0001)

        damaged_roads = []
        for feature in pre_event_geojson["features"]:
            pre_line = shape(feature["geometry"])
            # A road is considered damaged if it was predicted before the event,
            # it aligns with a known OSM road, but it was NOT predicted after the event.
            is_on_osm = pre_line.intersects(osm_union)
            is_in_post = post_union and pre_line.intersects(post_union)
            
            if is_on_osm and not is_in_post:
                damaged_roads.append(feature)

        result_geojson = {"type": "FeatureCollection", "features": damaged_roads}
        return jsonify({"geojson": result_geojson})

    except Exception as e:
        logging.error(f"Error during comparison: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected error occurred during analysis: {e}"}), 500

@app.route("/api/upload_geopackage", methods=["POST"])
def upload_geopackage():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    
    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if file and file.filename.lower().endswith('.gpkg'):
        try:
            # Read the geopackage file in memory
            gdf = gpd.read_file(file)
            
            # Reproject to WGS84 (EPSG:4326) if not already
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs("EPSG:4326")

            # Convert to GeoJSON
            geojson_data = json.loads(gdf.to_json())
            
            return jsonify(geojson_data)

        except Exception as e:
            logging.error(f"Failed to process GeoPackage file: {e}")
            return jsonify({"error": "Failed to process GeoPackage file.", "details": str(e)}), 500
    
    return jsonify({"error": "Invalid file type. Please upload a GeoPackage (.gpkg) file."}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)