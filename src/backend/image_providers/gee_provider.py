# backend/image_providers/gee_provider.py
import ee
import requests
from datetime import datetime
import logging
from typing import Dict, Any, Tuple

from .base_provider import ImageProvider

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

class GEEProvider(ImageProvider):
    """Google Earth Engine image provider."""

    def authenticate(self):
        global _ee_initialized
        if _ee_initialized:
            return

        project_id = self.credentials.get("project_id")
        if not project_id:
            raise ValueError("GEE Provider requires a 'project_id' in credentials.")

        try:
            # NOTE: For a server, you'd use a service account.
            # For this interactive tool, `ee.Authenticate()` is run once by the user
            # and `ee.Initialize()` is used on each startup.
            ee.Initialize(project=project_id)
            _ee_initialized = True
            logging.info(f"Earth Engine initialized successfully for project: {project_id}")
        except Exception as e:
            logging.error("Error during Earth Engine initialization: %s", e)
            # Instruct user how to authenticate if needed
            logging.warning("GEE AUTHENTICATION NEEDED")
            logging.warning("Please run 'earthengine authenticate' in your terminal.")
            raise e

    def download_image(self, lat_st: float, lon_st: float, lat_ed: float, lon_ed: float,
                       start_date: str, end_date: str, target_date: str,
                       output_path: str, scale: int, options: Dict[str, Any]) -> Tuple[str, str]:
        """
        Downloads the GEE image within a date range that is closest to a target date.
        """
        if not _ee_initialized:
            raise ValueError("Google Earth Engine is not authenticated.")

        satellite = options.get("satellite", "sentinel_2")
        if satellite not in SATELLITE_CONFIG:
            raise ValueError(f"Unsupported satellite for GEE: {satellite}")

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
            cloud_filter = int(current_options.get("cloudy_pixel_percentage", 10))
            image_collection = image_collection.filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_filter))
        elif satellite == "sentinel_2_nir":
            cloud_filter = int(current_options.get("cloudy_pixel_percentage", 10))
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

        return output_path, image_date_str, [
            lon_st, lat_st, lon_ed, lat_ed
        ]