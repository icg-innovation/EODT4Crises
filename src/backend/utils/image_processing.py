# backend/utils/image_processing.py
import rasterio
import numpy as np
from PIL import Image
import logging
from pyproj import Transformer

def process_geotiff_image(tif_path, save_path, satellite, size=(512, 512), brightness_factor=1.0):
    with rasterio.open(tif_path) as src:

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
        elif satellite == "maxar_imagery":
            # Maxar image is a browse preview (likely 8-bit RGB).
            # assume first three bands are RGB.
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))
            img = img.astype(np.uint8)
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
            bounds = src.bounds
            src_crs = src.crs

            if src_crs is None:
                logging.warning("GeoTIFF has no CRS information.")
                return None

            if src_crs.to_epsg() == 4326:
                lon_min, lat_min = bounds.left, bounds.bottom
                lon_max, lat_max = bounds.right, bounds.top
            else:
                transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
                lon_min, lat_min = transformer.transform(bounds.left, bounds.bottom)
                lon_max, lat_max = transformer.transform(bounds.right, bounds.top)

            return (lat_min, lat_max, lon_min, lon_max)
        except ImportError:
            logging.error("pyproj is not installed. Run 'pip install pyproj' to enable coordinate conversion.")
            return None
