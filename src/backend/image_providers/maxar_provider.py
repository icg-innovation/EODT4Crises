# backend/image_providers/maxar_provider.py

import requests
from datetime import datetime, timezone
import logging
from typing import Dict, Any, Tuple, List

from .base_provider import ImageProvider

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# The base URL for the Maxar Discovery API
MGP_API_BASE_URL = "https://api.maxar.com/discovery/v1"


class MaxarProvider(ImageProvider):
    """
    Maxar Geospatial Platform (MGP) image provider.
    This provider uses the STAC API to search for and download imagery.
    """
    
    def authenticate(self):
        """
        Validates the API key by setting up the session headers.
        """
        self.api_key = self.credentials.get("api_key")
        if not self.api_key:
            raise ValueError("Maxar Provider requires an 'api_key' in credentials.")
        
        # Set up the session with the API key and correct Accept header
        self.session = requests.Session()
        self.session.headers.update({
            "MAXAR-API-KEY": self.api_key,
            "Accept": "application/geo+json"
        })
        logging.info("Maxar provider session configured with MAXAR-API-KEY header.")

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
            
            acquisition_dt = datetime.fromisoformat(acquisition_date_str.replace("Z", "+00:00"))
            time_diff = abs((acquisition_dt - target_dt).total_seconds())
            
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                best_feature = feature
        
        return best_feature

    def download_image(self, lat_st: float, lon_st: float, lat_ed: float, lon_ed: float,
                       start_date: str, end_date: str, target_date: str,
                       output_path: str, scale: int, options: Dict[str, Any]) -> Tuple[str, str, List[float]]:
        """
        Searches the Maxar STAC API for an image and downloads the best match.
        Returns the file path, image date, and the image's bounding box.
        """
        # 1. Define the search URL for the 'imagery' sub-catalog
        search_url = f"{MGP_API_BASE_URL}/catalogs/imagery/search"
        
        # 2. Construct the parameters for a GET request
        datetime_range = f"{start_date}T00:00:00Z/{end_date}T23:59:59Z"
        bbox_list = [lon_st, lat_st, lon_ed, lat_ed]
        cloud_cover = int(options.get("cloud_cover", 10))

        # This dictionary will be converted to URL query parameters
        search_params = {
            "collections": "wv01,wv02,wv03,wv04,lg01,lg02", 
            "bbox": ",".join(map(str, bbox_list)),
            "datetime": datetime_range,
            "filter": f"eo:cloud_cover <= {cloud_cover}",
            "area-based-calc": "true",
            "limit": 100 
        }
        
        logging.info(f"Searching Maxar API with GET request at {search_url} with params: {search_params}")
        
        # 3. Make the search request using GET with params
        try:
            response = self.session.get(search_url, params=search_params)
            response.raise_for_status()
            search_results = response.json()
        except requests.exceptions.HTTPError as e:
            logging.error(f"Maxar API search failed with status {e.response.status_code}. Response: {e.response.text}")
            raise ConnectionError(f"Failed to connect to Maxar API: {e}")
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
        
        area_cloud_cover = best_image_feature["properties"].get("area:cloud_cover_percentage")
        if area_cloud_cover is not None:
             logging.info(f"Best image found. Acquisition date: {acquisition_date}. Cloud cover for AOI: {area_cloud_cover:.2f}%")
        else:
             logging.info(f"Best image found. Acquisition date: {acquisition_date}")
        
        # 5. Get the download URL from the 'assets' by checking common keys
        assets = best_image_feature.get("assets", {})
        download_url = None
        preferred_assets = ['browse', 'visual', 'data', 'analytic'] 
        for asset_key in preferred_assets:
            if asset_key in assets and 'href' in assets[asset_key]:
                download_url = assets[asset_key]['href']
                logging.info(f"Found downloadable asset '{asset_key}' for the image.")
                break

        if not download_url:
            logging.error(f"Could not find a suitable download URL. Available assets: {list(assets.keys())}")
            raise ValueError("Selected image feature does not have a downloadable asset URL under 'browse', 'visual', 'data', or 'analytic'.")
            
        logging.info(f"Downloading image from: {download_url}")
        
        # 6. Download the actual image file
        try:
            with self.session.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(output_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        except requests.exceptions.RequestException as e:
            logging.error(f"Maxar image download failed: {e}")
            raise ConnectionError(f"Failed to download image file from Maxar: {e}")
            
        logging.info(f"Successfully downloaded Maxar image to: {output_path}")
        
        image_date_str = datetime.fromisoformat(acquisition_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        
        # Return the path, date, and the bounding box from the STAC feature
        return output_path, image_date_str, best_image_feature.get('bbox')
