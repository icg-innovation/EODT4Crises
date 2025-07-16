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
from datetime import datetime

from torch.cuda import is_available

from pyproj import Transformer
import logging
from werkzeug.utils import secure_filename

from PIL import Image, ImageDraw
from shapely.geometry import shape, LineString
from shapely.ops import unary_union

from image_providers.provider_factory import get_provider
from utils.image_processing import process_geotiff_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

CURRENT_DIR = os.path.dirname(__file__)
SAM_ROAD_CONFIG_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "model_files", "spacenet_custom.yaml"))
SAM_ROAD_CHECKPOINT_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "model_files", "spacenet_vitb_256_e10.ckpt"))
SAM_ROAD_PROJECT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "data_processing"))
SPACENET_TRANSFORM_HEIGHT = 400
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
backend_static_folder = os.path.abspath(os.path.join(CURRENT_DIR, "static"))
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

def graph_to_geojson(adjacency_list, geotiff_path):
    features = []
    try:
        with rasterio.open(geotiff_path) as src:
            H_orig, W_orig = src.height, src.width
            transform = src.transform
            transformer = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)

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
    prefix = request.form.get('prefix', 'temp') # e.g., 'pre' or 'post'

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if file and file.filename.lower().endswith(('.tif', '.tiff')):
        # Use a consistent filename for the raw GeoTIFF
        raw_tiff_filename = f"temp_satellite_{prefix}.tif"
        save_path = os.path.join(backend_static_folder, raw_tiff_filename)
        
        try:
            file.save(save_path)
            logging.info(f"Uploaded file saved to: {save_path}")
            
            # Construct the URL that the frontend can use to access the file
            raw_tiff_url = f"/static/{raw_tiff_filename}"

            # Return the path and a generic date for the frontend to use
            return jsonify({
                "message": "Upload successful",
                "imageDate": "N/A (Local Upload)",
                "prefix": prefix,
                "rawTiffUrl": raw_tiff_url # Add this URL to the response
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

        # The provider now returns three values
        _, image_date, stac_bbox = provider.download_image(
            lat_st=lat_st, lon_st=lon_st, lat_ed=lat_ed, lon_ed=lon_ed,
            start_date=start_date, end_date=end_date, target_date=target_date,
            output_path=geotiff_path,
            scale=5,
            options=options
        )

        # Construct the URL for the downloaded GeoTIFF
        raw_tiff_url = f"/static/{geotiff_filename}"

        # Include the STAC bounding box and the raw TIFF URL in the response
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
    # Get the optional stac_bbox, which will be a comma-separated string
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

        if stac_bbox_str:
            # If a STAC bbox is provided (i.e., for Maxar), use it directly
            lon_min, lat_min, lon_max, lat_max = [float(b) for b in stac_bbox_str.split(',')]
            # Leaflet needs bounds in [[lat_min, lon_min], [lat_max, lon_max]] format
            leaflet_bounds = [[lat_min, lon_min], [lat_max, lon_max]]
            raw_bounds = {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max}
            # Still call process_geotiff_image to convert the file, but we'll ignore its returned bounds
            process_geotiff_image(tif_path=geotiff_path, save_path=png_path, satellite=satellite)
        else:
            # For GEE images, use the original method to extract bounds from the GeoTIFF
            bounds_4326 = process_geotiff_image(tif_path=geotiff_path, save_path=png_path, satellite=satellite)
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
        # Get data from the POST request body
        request_data = request.get_json()
        osm_geojson = request_data.get('osm_data')
        image_bounds_str = request_data.get('image_bounds')

        if not all([osm_geojson, image_bounds_str]):
            return jsonify({"error": "Missing 'osm_data' or 'image_bounds' in request body"}), 400

        # The Overpass API call is no longer needed here
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
    # This outer try block will catch any unexpected errors
    try:

        satellite_image_url = request.args.get("image_url")
        prefix = request.args.get("prefix", "pred")
        if not satellite_image_url:
            return jsonify({"error": "Missing 'image_url' parameter."}), 400

        input_filename = os.path.basename(satellite_image_url).split("?")[0]
        input_image_path = os.path.abspath(os.path.join(backend_static_folder, input_filename))
        if not os.path.exists(input_image_path):
            return jsonify({"error": f"Satellite image not found on server: {input_filename}"}), 404

        output_dir_name = f"sentinel_test_{prefix}"
        model_output_dir = os.path.join(SAM_ROAD_PROJECT_DIR, "save", output_dir_name)
        python_executable = sys.executable
        inference_script_path = os.path.join(SAM_ROAD_PROJECT_DIR, "inferencer.py")
        torch_device = "cuda" if is_available() else "cpu"

        command = [
            python_executable, inference_script_path,
            "--config", SAM_ROAD_CONFIG_PATH, "--checkpoint", SAM_ROAD_CHECKPOINT_PATH,
            "--device", torch_device, "--images", input_image_path, "--output_dir", output_dir_name,
        ]

        # This inner try block specifically handles the subprocess error
        try:
            subprocess.run(command, capture_output=True, text=True, check=True, cwd=SAM_ROAD_PROJECT_DIR)
        except subprocess.CalledProcessError as e:
            logging.error(f"Inference error: {e.stderr}")
            return jsonify({"error": "Failed to run road prediction model.", "details": e.stderr}), 500

        graph_path = os.path.join(model_output_dir, "graph", "0.p")
        mask_image_path = os.path.join(model_output_dir, "mask", "0_road.png")
        geotiff_path = os.path.join(backend_static_folder, f"temp_satellite_{prefix}.tif")
        if not all(os.path.exists(p) for p in [graph_path, mask_image_path, geotiff_path]):
            return jsonify({"error": "Model output or georeference file not found."}), 500

        with open(graph_path, "rb") as f:
            predicted_graph_data = pickle.load(f)

        predicted_roads_geojson = graph_to_geojson(predicted_graph_data, geotiff_path)

        unique_id = f"{prefix}_{int(time.time())}"
        mask_filename = f"predicted_mask_{unique_id}.png"
        shutil.copy(mask_image_path, os.path.join(backend_static_folder, mask_filename))

        return jsonify({"geojson": predicted_roads_geojson, "maskUrl": f"/static/{mask_filename}"})

    except Exception as e:
        logging.error(f"An unexpected error occurred in get_predicted_roads: {e}", exc_info=True)
        if isinstance(e, subprocess.CalledProcessError):
             return jsonify({"error": "Failed to run road prediction model.", "details": e.stderr}), 500
        return jsonify({"error": "An unexpected server error occurred.", "details": str(e)}), 500



@app.route("/api/compare_roads", methods=["POST"])
def compare_roads():
    try:
        # 1. Get OSM data from the POST request body
        request_data = request.get_json()
        osm_geojson = request_data.get('osm_data')
        if not osm_geojson:
            return jsonify({"error": "Missing 'osm_data' in request body"}), 400

        # 2. Load Pre- and Post-event prediction data from files
        geotiff_path_pre = os.path.join(backend_static_folder, "temp_satellite_pre.tif")
        graph_path_pre = os.path.join(SAM_ROAD_PROJECT_DIR, "save", "sentinel_test_pre", "graph", "0.p")
        with open(graph_path_pre, "rb") as f:
            graph_data_pre = pickle.load(f)
        pre_event_geojson = graph_to_geojson(graph_data_pre, geotiff_path_pre)

        geotiff_path_post = os.path.join(backend_static_folder, "temp_satellite_post.tif")
        graph_path_post = os.path.join(SAM_ROAD_PROJECT_DIR, "save", "sentinel_test_post", "graph", "0.p")
        with open(graph_path_post, "rb") as f:
            graph_data_post = pickle.load(f)
        post_event_geojson = graph_to_geojson(graph_data_post, geotiff_path_post)
    except FileNotFoundError as e:
        logging.error(f"Prediction file not found: {e}")
        return jsonify({"error": "A prediction file was not found. Please run both detections first."}), 404
    except Exception as e:
        logging.error(f"Error loading prediction data: {e}")
        return jsonify({"error": "Could not load prediction data."}), 500

    # 3. Perform the comparison
    try:
        post_lines = [shape(feature["geometry"]) for feature in post_event_geojson["features"]]
        osm_lines = [shape(feature["geometry"]) for feature in osm_geojson["features"]]
        if not osm_lines:
            return jsonify({"error": "No OSM roads found in the data to use as a reference."}), 404

        post_union = unary_union(post_lines)
        osm_union = unary_union(osm_lines)

        post_buffer = post_union.buffer(0.0002)
        osm_buffer = osm_union.buffer(0.0002)

        damaged_roads = []
        for feature in pre_event_geojson["features"]:
            pre_line = shape(feature["geometry"])
            is_on_osm = pre_line.intersects(osm_buffer)
            is_in_post = pre_line.intersects(post_buffer)
            if is_on_osm and not is_in_post:
                damaged_roads.append(feature)

        result_geojson = {"type": "FeatureCollection", "features": damaged_roads}
        return jsonify({"geojson": result_geojson})

    except Exception as e:
        logging.error(f"Error during comparison: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected error occurred during analysis: {e}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)