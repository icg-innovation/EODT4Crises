# backend/image_providers/base_provider.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple

class ImageProvider(ABC):
    """
    Abstract Base Class for all satellite image providers.
    It defines the common interface for authenticating and downloading data.
    """

    def __init__(self, credentials: Dict[str, Any]):
        """
        Initializes the provider with the necessary credentials.
        
        Args:
            credentials (Dict[str, Any]): A dictionary containing API keys,
                                          project IDs, etc.
        """
        self.credentials = credentials
        self.authenticate()

    @abstractmethod
    def authenticate(self):
        """
        Handles the authentication for the specific provider.
        Should raise an exception if authentication fails.
        """
        pass

    @abstractmethod
    def download_image(self, lat_st: float, lon_st: float, lat_ed: float, lon_ed: float,
                       start_date: str, end_date: str, target_date: str,
                       output_path: str, scale: int, options: Dict[str, Any]) -> Tuple[str, str]:
        """
        Downloads an image for a given area and date range.

        Args:
            // ... (all your coordinate and date arguments)
            output_path (str): The local path to save the downloaded GeoTIFF.
            scale (int): The image resolution in meters per pixel.
            options (Dict[str, Any]): Provider-specific options like bands,
                                      polarization, cloud cover, etc.

        Returns:
            Tuple[str, str]: A tuple containing the final output path and the
                             exact date of the acquired image (e.g., "2024-03-25").
        """
        pass