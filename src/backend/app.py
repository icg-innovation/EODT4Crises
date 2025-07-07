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
from data_processing.gee_downloader import (
    authenticate_earth_engine,
    download_sentinel_image,
    process_geotiff_image,
)
from pyproj import Transformer
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
CURRENT_DIR = os.path.dirname(__file__)
SAM_ROAD_CONFIG_PATH = os.path.abspath(
    os.path.join(CURRENT_DIR, "model_files", "spacenet_custom.yaml")
)  # TODO: change to south_uk_custom.yaml
SAM_ROAD_CHECKPOINT_PATH = os.path.abspath(
    os.path.join(CURRENT_DIR, "model_files", "spacenet_vitb_256_e10.ckpt")
)
SAM_ROAD_PROJECT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "data_processing"))
SPACENET_TRANSFORM_HEIGHT = 400
backend_static_folder = os.path.abspath(os.path.join(CURRENT_DIR, "static"))

# -----------------------------------------------------------------------------
# Startup Cleanup
# -----------------------------------------------------------------------------
# Deletes old generated images from the static folder to prevent clutter.
print("--- Cleaning up old generated files ---")
if os.path.exists(backend_static_folder):
    # Find all files matching the patterns for generated images
    files_to_delete = glob.glob(
        os.path.join(backend_static_folder, "satellite_image_*.png")
    )
    files_to_delete += glob.glob(
        os.path.join(backend_static_folder, "predicted_mask_*.png")
    )

    for f_path in files_to_delete:
        try:
            os.remove(f_path)
            print(f"Deleted: {os.path.basename(f_path)}")
        except OSError as e:
            print(f"Error deleting file {f_path}: {e}")
else:
    # Create the static directory if it doesn't exist
    os.makedirs(backend_static_folder)

# -----------------------------------------------------------------------------
# GEE Authentication
# -----------------------------------------------------------------------------
try:
    authenticate_earth_engine(
        "uksa-training-course-materials"
    )  # TODO: change to a generic project ID or remove if not needed
except Exception as e:
    print(f"Could not initialize Google Earth Engine: {e}")

# -----------------------------------------------------------------------------
# App Initialization
# -----------------------------------------------------------------------------
frontend_static_folder = os.path.abspath(
    os.path.join(CURRENT_DIR, "..", "frontend", "public")
)
backend_static_folder = os.path.abspath(os.path.join(CURRENT_DIR, "static"))
app = Flask(__name__, static_folder=frontend_static_folder, static_url_path="")

logging.info(f"Serving static files from frontend: {frontend_static_folder}")


@app.route("/static/<path:filename>")
def backend_static(filename):
    return send_from_directory(backend_static_folder, filename)


CORS(app)


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
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


def graph_to_geojson(adjacency_list, geotiff_path):
    features = []
    try:
        with rasterio.open(geotiff_path) as src:
            H_orig, W_orig = src.height, src.width
            H_new, W_new = 512, 512
            scale_x = W_orig / W_new
            scale_y = H_orig / H_new
            scaled_transform = src.transform * Affine.scale(scale_x, scale_y)
            transformer = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)

            for source_node_yx, dest_nodes_yx in adjacency_list.items():
                for dest_node_yx in dest_nodes_yx:
                    source_y_flipped, source_x = source_node_yx
                    dest_y_flipped, dest_x = dest_node_yx
                    source_y = SPACENET_TRANSFORM_HEIGHT - source_y_flipped
                    dest_y = SPACENET_TRANSFORM_HEIGHT - dest_y_flipped

                    start_x_proj, start_y_proj = (
                        source_x + 0.5,
                        source_y + 0.5,
                    ) * scaled_transform
                    end_x_proj, end_y_proj = (
                        dest_x + 0.5,
                        dest_y + 0.5,
                    ) * scaled_transform

                    start_lon, start_lat = transformer.transform(
                        start_x_proj, start_y_proj
                    )
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


# -----------------------------------------------------------------------------
# API and Frontend Endpoints
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/get_roads", methods=["GET"])
def get_roads():
    bbox = request.args.get("bbox")
    logging.info(f"Received request for OSM roads with bbox: {bbox}")
    types_str = request.args.get("types")
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
    overpass_query = f"""[out:json][timeout:25];(way["highway"~"^({overpass_types})$"]({overpass_bbox}););out body;>;out skel qt;"""
    try:
        response = requests.get(overpass_url, params={"data": overpass_query})
        logging.info(f"Overpass API response status: {response.status_code}")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch data from Overpass API: {e}"}), 502
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse response from Overpass API."}), 500
    return jsonify(overpass_to_geojson(data))


@app.route("/api/download_satellite_image", methods=["GET"])
def download_satellite_image():
    bbox = request.args.get("bbox")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    logging.info(
        f"Received request to download satellite image for bbox: {bbox}, dates: {start_date} to {end_date}"
    )
    if not all([bbox, start_date, end_date]):
        return jsonify(
            {"error": "Missing 'bbox', 'start_date', or 'end_date' query parameter"}
        ), 400
    try:
        lon_st, lat_st, lon_ed, lat_ed = [float(coord) for coord in bbox.split(",")]
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except (ValueError, IndexError) as e:
        logging.error(f"Invalid 'bbox' or 'date' format. Error: {e}")
        return jsonify({"error": "Invalid 'bbox' or 'date' format."}), 400
    geotiff_path = os.path.join(backend_static_folder, "temp_satellite.tif")
    try:
        logging.info(f"Attempting to download Sentinel image to {geotiff_path}")
        _, image_date = download_sentinel_image(
            lat_st,
            lon_st,
            lat_ed,
            lon_ed,
            scale=10,
            output_path=geotiff_path,
            start_date=start_date,
            end_date=end_date,
        )
        return jsonify({"message": "Download successful", "imageDate": image_date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/process_satellite_image", methods=["GET"])
def process_satellite_image():
    brightness_str = request.args.get("brightness", "1.0")
    geotiff_path = os.path.join(backend_static_folder, "temp_satellite.tif")
    logging.info(
        f"Received request to process satellite image. Brightness: {brightness_str}"
    )
    if not os.path.exists(geotiff_path):
        logging.warning(f"GeoTIFF not found at {geotiff_path}")
        return jsonify(
            {
                "error": "No satellite data found on server. Please draw a box to download data first."
            }
        ), 404
    try:
        brightness = float(brightness_str)
        unique_id = int(time.time())
        png_filename = f"satellite_image_{unique_id}.png"
        png_path = os.path.join(backend_static_folder, png_filename)
        logging.info(
            f"Processing GeoTIFF to PNG: {png_path} with brightness {brightness}"
        )
        bounds_4326 = process_geotiff_image(
            geotiff_path, save_path=png_path, brightness_factor=brightness
        )
        if bounds_4326 is None:
            logging.warning(
                "Image processing resulted in too many black pixels or invalid data."
            )
            return jsonify(
                {"error": "Image skipped: too many black pixels or invalid data."}
            ), 422
        leaflet_bounds = [
            [bounds_4326[0], bounds_4326[2]],
            [bounds_4326[1], bounds_4326[3]],
        ]
        image_url = f"/static/{png_filename}"
        return jsonify({"imageUrl": image_url, "bounds": leaflet_bounds})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/get_predicted_roads", methods=["GET"])
def get_predicted_roads():
    logging.info("Received request for predicted roads.")
    satellite_image_url = request.args.get("image_url")
    if not satellite_image_url:
        logging.warning("Missing 'image_url' parameter for predicted roads request.")
        return jsonify({"error": "Missing 'image_url' parameter."}), 400

    input_filename = os.path.basename(satellite_image_url).split("?")[
        0
    ]  # Remove cache buster
    input_image_path = os.path.abspath(
        os.path.join(backend_static_folder, input_filename)
    )

    if not os.path.exists(input_image_path):
        logging.warning(f"Satellite image not found for prediction: {input_image_path}")
        return jsonify(
            {"error": f"Satellite image not found on server: {input_filename}"}
        ), 404

    try:
        unique_id = input_filename.split("_")[-1].split(".")[0]
        logging.info(
            f"Received request for predicted roads for image: {input_image_path}"
        )
    except IndexError:
        return jsonify({"error": "Invalid image filename format."}), 400

    output_dir_name = "sentinel_test"
    model_output_dir = os.path.join(SAM_ROAD_PROJECT_DIR, "save", output_dir_name)
    python_executable = sys.executable
    inference_script_path = os.path.join(SAM_ROAD_PROJECT_DIR, "inferencer.py")
    command = [
        python_executable,
        inference_script_path,
        "--config",
        SAM_ROAD_CONFIG_PATH,
        "--checkpoint",
        SAM_ROAD_CHECKPOINT_PATH,
        "--device",
        "cpu",
        "--images",
        input_image_path,
        "--output_dir",
        output_dir_name,
    ]
    try:
        logging.info(f"Running inference command: {' '.join(command)}")
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            cwd=SAM_ROAD_PROJECT_DIR,
        )
        logging.info("Inference command completed successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to run road prediction model. Stderr: {e.stderr}")
        return jsonify(
            {"error": "Failed to run road prediction model.", "details": e.stderr}
        ), 500

    graph_path = os.path.join(model_output_dir, "graph", "0.p")
    mask_image_path = os.path.join(model_output_dir, "mask", "0_road.png")
    geotiff_path = os.path.join(backend_static_folder, "temp_satellite.tif")

    if not os.path.exists(graph_path) or not os.path.exists(mask_image_path):
        logging.error(
            f"Model output missing. Graph: {graph_path}, Mask: {mask_image_path}"
        )
        return jsonify(
            {"error": "Model ran, but output graph or mask file was not found."}
        ), 500
    if not os.path.exists(geotiff_path):
        return jsonify(
            {"error": "Temporary GeoTIFF not found, cannot georeference data."}
        ), 500

    try:
        with open(graph_path, "rb") as f:
            predicted_graph_data = pickle.load(f)
        logging.info(f"Loaded predicted graph from {graph_path}")
        predicted_roads_geojson = graph_to_geojson(predicted_graph_data, geotiff_path)

        mask_filename = f"predicted_mask_{unique_id}.png"
        shutil.copy(mask_image_path, os.path.join(backend_static_folder, mask_filename))
        logging.info(
            f"Copied predicted mask to {os.path.join(backend_static_folder, mask_filename)}"
        )
        return jsonify(
            {"geojson": predicted_roads_geojson, "maskUrl": f"/static/{mask_filename}"}
        )
    except Exception as e:
        logging.error(f"Failed to process model output: {e}")
        return jsonify({"error": f"Failed to process model output: {str(e)}"}), 500


# -----------------------------------------------------------------------------
# Main Execution Block
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)
