import ee
import requests
from datetime import datetime
import rasterio
import numpy as np
from PIL import Image
import os
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

SATELLITE_CONFIG = {
    "sentinel_2": {
        "collection": "COPERNICUS/S2_HARMONIZED",
        "bands": ["B4", "B3", "B2"],
        "default_options": {"cloudy_pixel_percentage": 10}
    },
    "sentinel_2_nir": {
        "collection": "COPERNICUS/S2_HARMONIZED",
        "bands": ["B4", "B8", "B3"],
        "default_options": {"cloudy_pixel_percentage": 10}
    },
    "sentinel_1": {
        "collection": "COPERNICUS/S1_GRD",
        "bands": ["VV"],
        "default_options": {"polarization": "VV", "instrument_mode": "IW"}
    }
}

_ee_initialized = False

def authenticate_earth_engine(project_id):
    global _ee_initialized
    if _ee_initialized:
        return
    try:
        ee.Authenticate()
        ee.Initialize(project=project_id)
        _ee_initialized = True
        logging.info("Earth Engine authenticated successfully.")
    except Exception as e:
        logging.error("Error during Earth Engine authentication: %s", e)
        raise e

def download_gee_image_near_date(
    lat_st, lon_st, lat_ed, lon_ed, scale, output_path, start_date, end_date, target_date, satellite, options=None
):
    """
    Downloads the GEE image within a date range that is closest to a target date.
    """
    if not _ee_initialized:
        raise ValueError("Google Earth Engine is not authenticated.")
    if satellite not in SATELLITE_CONFIG:
        raise ValueError(f"Unsupported satellite: {satellite}")

    config = SATELLITE_CONFIG[satellite]
    current_options = config["default_options"].copy()
    if options:
        current_options.update(options)

    region = ee.Geometry.Polygon([
        [[lon_st, lat_ed], [lon_st, lat_st], [lon_ed, lat_st], [lon_ed, lat_ed], [lon_st, lat_ed]]
    ])

    image_collection = (
        ee.ImageCollection(config["collection"])
        .filterDate(start_date, end_date)
        .filterBounds(region)
    )

    if satellite == "sentinel_2":
        cloud_filter = current_options.get("cloudy_pixel_percentage", 10)
        image_collection = image_collection.filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_filter))
    elif satellite == "sentinel_2_nir":
        cloud_filter = current_options.get("cloudy_pixel_percentage", 10)
        image_collection = image_collection.filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_filter))
    elif satellite == "sentinel_1":
        polarization = current_options.get("polarization", "VV")
        mode = current_options.get("instrument_mode", "IW")
        image_collection = image_collection.filter(ee.Filter.listContains('transmitterReceiverPolarisation', polarization))
        image_collection = image_collection.filter(ee.Filter.eq('instrumentMode', mode))

    collection_size = image_collection.size().getInfo()
    if collection_size == 0:
        raise ValueError(f"No '{satellite}' image found for the specified criteria and date range ({start_date} to {end_date}).")

    target_date_millis = ee.Date(target_date).millis()
    def set_time_diff(image):
        return image.set('time_diff', ee.Number(image.get('system:time_start')).subtract(target_date_millis).abs())
    image_collection_with_diff = image_collection.map(set_time_diff)

    image = image_collection_with_diff.sort('time_diff').first()

    image_date_info = image.get("system:time_start").getInfo()
    image_date_str = datetime.utcfromtimestamp(image_date_info / 1000).strftime("%Y-%m-%d")
    logging.info(f"Found image from date: {image_date_str} (closest to {target_date})")

    bands_to_download = options.get("bands", config["bands"])
    url = image.select(bands_to_download).getDownloadURL({
        "region": region, "scale": scale, "crs": "EPSG:3857", "format": "GEO_TIFF"
    })

    response = requests.get(url)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)
    logging.info(f"Downloaded image to {output_path}")

    return output_path, image_date_str

def process_geotiff_image(tif_path, save_path, satellite, size=(512, 512), brightness_factor=1.0):
    with rasterio.open(tif_path) as src:
        bounds_3857 = src.bounds

        if satellite == "sentinel_2":
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))
            divisor = 10000.0 / brightness_factor
            img = np.clip(img / divisor, 0, 1) * 255
            img = img.astype(np.uint8)
        elif satellite == "sentinel_2_nir":
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))
            divisor = 10000.0 / brightness_factor
            img = np.clip(img / divisor, 0, 1) * 255
            img = img.astype(np.uint8)
        elif satellite == "sentinel_1":
            img = src.read(1)
            p2, p98 = np.percentile(img, (2, 98))
            img = np.clip((img - p2) * (255.0 / (p98 - p2)), 0, 255)
            img = img.astype(np.uint8)
            img = np.stack([img]*3, axis=-1)
        else:
            raise ValueError(f"Processing not implemented for satellite: {satellite}")

        # img_resized = Image.fromarray(img).resize(size, Image.BILINEAR)

        if np.all(img == 0, axis=-1).sum() > 0.97 * img.size / 3:
            logging.warning("Image has excessive black pixels, skipping.")
            return None

        # Create a PIL Image from the original numpy array and save it.
        Image.fromarray(img).save(save_path)
        logging.info("Saved processed image to: %s", save_path)
        logging.info("... pixel size: %s", img.shape)

        try:
            from pyproj import Transformer
            transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
            lon_min, lat_min = transformer.transform(bounds_3857.left, bounds_3857.bottom)
            lon_max, lat_max = transformer.transform(bounds_3857.right, bounds_3857.top)
            return (lat_min, lat_max, lon_min, lon_max)
        except ImportError:
            logging.error("pyproj is not installed. Run 'pip install pyproj' to enable coordinate conversion.")
            return None