# backend/image_providers/maxar_provider.py

import requests
import os
from datetime import datetime, timezone
import logging
from typing import Dict, Any, Tuple

from .base_provider import ImageProvider

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# NOTE: The MGP API base URL. This should be confirmed from Maxar's documentation.
MGP_API_BASE_URL = "https://api.maxar.com"


class MaxarProvider(ImageProvider):
    """
    Maxar Geospatial Platform (MGP) image provider.
    This provider uses the STAC API to search for and download imagery.
    """
    
    def authenticate(self):
        """
        Validates the API key by setting up the session headers.
        A proper implementation would make a test call to a 'ping' or 'health' endpoint.
        """
        self.api_key = self.credentials.get("api_key")
        if not self.api_key:
            raise ValueError("Maxar Provider requires an 'api_key' in credentials.")
        
        # We will use this session object for all subsequent requests.
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        logging.info("Maxar provider session configured with API key.")

    def _find_best_image(self, features: list, target_date_str: str) -> Dict[str, Any]:
        """
        Finds the best image from a list of STAC features based on the closest acquisition date.
        """
        target_dt = datetime.fromisoformat(target_date_str).replace(tzinfo=timezone.utc)
        best_feature = None
        min_time_diff = float('inf')

        for feature in features:
            acquisition_date_str = feature.get("properties", {}).get("datetime")
            if not acquisition_date_str:
                continue
            
            # Ensure the acquisition date is timezone-aware for correct comparison
            acquisition_dt = datetime.fromisoformat(acquisition_date_str.replace("Z", "+00:00"))
            
            time_diff = abs((acquisition_dt - target_dt).total_seconds())
            
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                best_feature = feature
        
        return best_feature

    def download_image(self, lat_st: float, lon_st: float, lat_ed: float, lon_ed: float,
                       start_date: str, end_date: str, target_date: str,
                       output_path: str, scale: int, options: Dict[str, Any]) -> Tuple[str, str]:
        """
        Searches the Maxar STAC API for an image and downloads the best match.
        """
        # 1. Define the search endpoint for the MGP STAC API
        search_url = f"{MGP_API_BASE_URL}/stac/v1/search"
        
        # 2. Construct the search payload
        # The date range needs to be in ISO 8601 format
        datetime_range = f"{start_date}T00:00:00Z/{end_date}T23:59:59Z"
        
        # The bounding box for the search
        bbox = [lon_st, lat_st, lon_ed, lat_ed]
        
        # Cloud cover is typically represented as a value between 0 and 1
        cloud_cover = int(options.get("cloud_cover", 10)) / 100.0

        # This is a standard STAC API search payload
        search_payload = {
            "collections": ["maxar-imagery"], # This collection name should be verified
            "bbox": bbox,
            "datetime": datetime_range,
            "query": {
                "eo:cloud_cover": {
                    "lte": cloud_cover # "lte" means "less than or equal to"
                }
            },
            "limit": 25 # Request a reasonable number of results to find the best one
        }
        
        logging.info(f"Searching Maxar API with payload: {search_payload}")
        
        # 3. Make the search request
        try:
            response = self.session.post(search_url, json=search_payload)
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            search_results = response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Maxar API search request failed: {e}")
            raise ConnectionError(f"Failed to connect to Maxar API: {e}")

        features = search_results.get("features", [])
        if not features:
            raise ValueError(f"No 'Maxar' image found for the specified criteria and date range ({start_date} to {end_date}).")
            
        # 4. Find the best image from the results
        best_image_feature = self._find_best_image(features, target_date)
        if not best_image_feature:
            raise ValueError("Found images, but could not determine the best match (check datetime properties).")

        acquisition_date = best_image_feature["properties"]["datetime"]
        logging.info(f"Best image found. Acquisition date: {acquisition_date}")
        
        # 5. Get the download URL from the 'assets'
        # We look for the 'visual' asset, which is typically the display-ready image.
        # The asset key might be different, e.g., 'data', 'analytic', etc.
        assets = best_image_feature.get("assets", {})
        download_url = assets.get("visual", {}).get("href")
        
        if not download_url:
            raise ValueError("Selected image feature does not have a downloadable 'visual' asset URL.")
            
        logging.info(f"Downloading image from: {download_url}")
        
        # 6. Download the actual image file
        try:
            # Use streaming to handle potentially large image files efficiently
            with self.session.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(output_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        except requests.exceptions.RequestException as e:
            logging.error(f"Maxar image download failed: {e}")
            raise ConnectionError(f"Failed to download image file from Maxar: {e}")
            
        logging.info(f"Successfully downloaded Maxar image to: {output_path}")
        
        # Return the path and the exact date of the acquired image
        image_date_str = datetime.fromisoformat(acquisition_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        return output_path, image_date_str
