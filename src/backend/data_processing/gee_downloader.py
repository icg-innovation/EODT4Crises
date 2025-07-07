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

_ee_initialized = False


def authenticate_earth_engine(project_id):
    global _ee_initialized
    if _ee_initialized:
        logging.debug("Earth Engine already initialized in this process.")
        return
    try:
        if not ee.data._credentials:
            logging.info("Authenticating Earth Engine (may open browser)...")
            ee.Authenticate()
        ee.Initialize(project=project_id)
        _ee_initialized = True
        logging.info("Earth Engine authenticated successfully.")
    except Exception as e:
        logging.error(
            "Error during Earth Engine authentication or initialization: %s", e
        )
        raise e


def download_sentinel_image(
    lat_st, lon_st, lat_ed, lon_ed, scale, output_path, start_date, end_date
):
    """
    Download a Sentinel-2 image for the specified bounding box and date range.
    Returns a tuple of (output_path, image_date_string).
    """
    if not _ee_initialized:
        raise ValueError(
            "Google Earth Engine is not authenticated. Please call authenticate_earth_engine(project_id) first."
        )

    coords = [
        [
            [lon_st, lat_ed],
            [lon_st, lat_st],
            [lon_ed, lat_st],
            [lon_ed, lat_ed],
            [lon_st, lat_ed],
        ]
    ]
    region = ee.Geometry.Polygon(coords)

    satellite_collection = {
        "sentinel_2": "COPERNICUS/S2_HARMONIZED",
        "sentinel_1": "COPERNICUS/S2_HARMONIZED",
    }

    sentinel_collection = (
        ee.ImageCollection(satellite_collection["sentinel_2"])
        .filterDate(start_date, end_date)
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 10))
    )

    collection_size = sentinel_collection.size().getInfo()
    if collection_size == 0:
        raise ValueError(
            f"No cloud-free Sentinel-2 image found for the date range: {start_date} to {end_date}"
        )

    # Sort descending by acquisition time so the most recent image comes first
    sorted_collection = sentinel_collection.sort("system:time_start", False)

    # Get the most recent image
    image = sorted_collection.first()

    image_date_info = image.get("system:time_start").getInfo()
    image_date_str = datetime.utcfromtimestamp(image_date_info / 1000).strftime(
        "%Y-%m-%d"
    )
    logging.info("Found image from date: %s", image_date_str)

    url = image.select(["B4", "B3", "B2"]).getDownloadURL(
        {"region": region, "scale": scale, "crs": "EPSG:3857", "format": "GEO_TIFF"}
    )

    response = requests.get(url)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logging.info("Downloaded image size: %.2f MB", file_size_mb)

    return output_path, image_date_str


def process_geotiff_image(tif_path, save_path, size=(512, 512), brightness_factor=1.0):
    """
    Processes a GeoTIFF, converts it to PNG, and returns its bounds.
    """
    with rasterio.open(tif_path) as src:
        bounds_3857 = src.bounds
        img = src.read([1, 2, 3])
        img = np.transpose(img, (1, 2, 0))

        # Adjust brightness based on the factor from the slider
        divisor = 10000.0 / brightness_factor
        img = np.clip(img / divisor, 0, 1) * 255

        img = img.astype(np.uint8)
        img_resized = Image.fromarray(img).resize(size, Image.BILINEAR)

        arr = np.array(img_resized)
        black_mask = np.all(arr == 0, axis=-1)
        black_pixels = np.sum(black_mask)
        total_pixels = arr.shape[0] * arr.shape[1]
        if black_pixels > 0.97 * total_pixels:
            logging.warning("Too many black pixels, skipping.")
            return None

        img_resized.save(save_path)
        logging.info("Saved image: %s", save_path)

        try:
            from pyproj import Transformer

            transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
            lon_min, lat_min = transformer.transform(
                bounds_3857.left, bounds_3857.bottom
            )
            lon_max, lat_max = transformer.transform(bounds_3857.right, bounds_3857.top)
            return (lat_min, lat_max, lon_min, lon_max)
        except ImportError:
            logging.error(
                "pyproj is not installed. Cannot convert coordinates. Please run 'pip install pyproj'"
            )
            return None
