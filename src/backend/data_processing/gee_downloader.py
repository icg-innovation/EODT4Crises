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

# --- New Satellite Configuration ---
# Defines the properties for each supported satellite. This makes the script modular.
SATELLITE_CONFIG = {
    "sentinel_2": {
        "collection": "COPERNICUS/S2_HARMONIZED",
        "bands": ["B4", "B3", "B2"],  # Default to RGB for visualization
        "default_options": {
            "cloudy_pixel_percentage": 10
        }
    },
    "sentinel_1": {
        "collection": "COPERNICUS/S1_GRD",
        "bands": ["VV"],  # Default to VV polarization
        "default_options": {
            "polarization": "VV", # Can be 'VV' or 'VH'
            "instrument_mode": "IW"
        }
    }
}


_ee_initialized = False


def authenticate_earth_engine(project_id):
    """Authenticates and initializes the Earth Engine API."""
    global _ee_initialized
    if _ee_initialized:
        logging.debug("Earth Engine already initialized.")
        return
    try:
        # Check for existing credentials, otherwise authenticate
        if not ee.data._credentials:
            logging.info("Authenticating Earth Engine (may open browser)...")
            ee.Authenticate()
        ee.Initialize(project=project_id)
        _ee_initialized = True
        logging.info("Earth Engine authenticated successfully.")
    except Exception as e:
        logging.error("Error during Earth Engine authentication: %s", e)
        raise e


def download_gee_image(
    lat_st, lon_st, lat_ed, lon_ed, scale, output_path, start_date, end_date, satellite, options=None
):
    """
    Downloads a Google Earth Engine image based on the selected satellite and options.

    Args:
        lat_st, lon_st, lat_ed, lon_ed (float): Bounding box coordinates.
        scale (int): The resolution in meters per pixel.
        output_path (str): The path to save the downloaded GeoTIFF.
        start_date, end_date (str): Date range in 'YYYY-MM-DD' format.
        satellite (str): The name of the satellite (e.g., 'sentinel_2', 'sentinel_1').
        options (dict, optional): Satellite-specific options to override defaults.

    Returns:
        A tuple of (output_path, image_date_string).
    """
    if not _ee_initialized:
        raise ValueError("Google Earth Engine is not authenticated. Call authenticate_earth_engine() first.")

    if satellite not in SATELLITE_CONFIG:
        raise ValueError(f"Unsupported satellite: {satellite}. Supported satellites are {list(SATELLITE_CONFIG.keys())}")

    config = SATELLITE_CONFIG[satellite]
    # Merge user options with defaults
    current_options = config["default_options"].copy()
    if options:
        current_options.update(options)
    
    # Define the region of interest
    region = ee.Geometry.Polygon([
        [[lon_st, lat_ed], [lon_st, lat_st], [lon_ed, lat_st], [lon_ed, lat_ed], [lon_st, lat_ed]]
    ])

    # Start building the image collection query
    image_collection = (
        ee.ImageCollection(config["collection"])
        .filterDate(start_date, end_date)
        .filterBounds(region)
    )

    # --- Apply satellite-specific filters based on options ---
    if satellite == "sentinel_2":
        cloud_filter = current_options.get("cloudy_pixel_percentage", 10)
        image_collection = image_collection.filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_filter))
        logging.info(f"Applying Sentinel-2 filter: Cloudy Pixel Percentage < {cloud_filter}%")

    elif satellite == "sentinel_1":
        polarization = current_options.get("polarization", "VV")
        mode = current_options.get("instrument_mode", "IW")
        image_collection = image_collection.filter(ee.Filter.listContains('transmitterReceiverPolarisation', polarization))
        image_collection = image_collection.filter(ee.Filter.eq('instrumentMode', mode))
        logging.info(f"Applying Sentinel-1 filter: Polarization = {polarization}, Instrument Mode = {mode}")

    # Check if any images were found
    collection_size = image_collection.size().getInfo()
    if collection_size == 0:
        raise ValueError(f"No '{satellite}' image found for the specified criteria and date range.")

    # Sort to get the most recent image
    image = image_collection.sort("system:time_start", False).first()

    # Get image metadata
    image_date_info = image.get("system:time_start").getInfo()
    image_date_str = datetime.utcfromtimestamp(image_date_info / 1000).strftime("%Y-%m-%d")
    logging.info(f"Found image from date: {image_date_str}")
    
    # Select bands based on config or options
    bands_to_download = options.get("bands", config["bands"])

    # Get the download URL
    url = image.select(bands_to_download).getDownloadURL({
        "region": region, "scale": scale, "crs": "EPSG:3857", "format": "GEO_TIFF"
    })

    # Download the file
    response = requests.get(url)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)
    logging.info(f"Downloaded image to {output_path}")

    return output_path, image_date_str


def process_geotiff_image(tif_path, save_path, satellite, size=(512, 512), brightness_factor=1.0):
    """
    Processes a GeoTIFF, converts it to PNG based on satellite type, and returns its bounds.
    """
    with rasterio.open(tif_path) as src:
        bounds_3857 = src.bounds
        
        if satellite == "sentinel_2":
            # Read RGB bands
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))
            # Scale optical imagery
            divisor = 10000.0 / brightness_factor
            img = np.clip(img / divisor, 0, 1) * 255
            img = img.astype(np.uint8)

        elif satellite == "sentinel_1":
            # Read single SAR band
            img = src.read(1)
            # Normalize SAR data for visualization (e.g., from dB to 8-bit)
            # This is a simple percentile stretch, more advanced methods could be used
            p2, p98 = np.percentile(img, (2, 98))
            img = np.clip((img - p2) * (255.0 / (p98 - p2)), 0, 255)
            img = img.astype(np.uint8)
            # Convert grayscale to RGB for saving as PNG
            img = np.stack([img]*3, axis=-1)

        else:
            raise ValueError(f"Processing not implemented for satellite: {satellite}")

        # Resize the image
        img_resized = Image.fromarray(img).resize(size, Image.BILINEAR)

        # Check for excessive black pixels
        arr = np.array(img_resized)
        if np.all(arr == 0, axis=-1).sum() > 0.97 * arr.size / 3:
            logging.warning("Image has excessive black pixels, skipping.")
            return None

        # Save the final PNG
        img_resized.save(save_path)
        logging.info("Saved processed image to: %s", save_path)

        # Reproject bounds to standard Lat/Lon
        try:
            from pyproj import Transformer
            transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
            lon_min, lat_min = transformer.transform(bounds_3857.left, bounds_3857.bottom)
            lon_max, lat_max = transformer.transform(bounds_3857.right, bounds_3857.top)
            return (lat_min, lat_max, lon_min, lon_max)
        except ImportError:
            logging.error("pyproj is not installed. Run 'pip install pyproj' to enable coordinate conversion.")
            return None