# backend/image_providers/local_provider.py

import logging
from typing import Tuple, List

from .base_provider import ImageProvider

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

class LocalProvider(ImageProvider):
    """
    Provider for handling user-uploaded local GeoTIFF files.
    """
    
    def authenticate(self):
        """
        No authentication is needed for local files.
        """
        logging.info("Local provider initialized. No authentication required.")
        pass

    def download_image(self, *args, **kwargs) -> Tuple[str, str, List[float]]:
        """
        This method is not used for the local provider, as the file is uploaded
        directly by the user. It raises a NotImplementedError to prevent misuse.
        """
        raise NotImplementedError("'download_image' is not applicable for the LocalProvider.")
