# backend/utils/image_processing.py
import rasterio
import numpy as np
from PIL import Image
import logging
from pyproj import Transformer

def process_geotiff_image(tif_path, save_path, satellite, size=(512, 512), brightness_factor=1.0, normalize=True):
    with rasterio.open(tif_path) as src:

        if satellite == "sentinel_2":
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))

            if normalize:
                divisor = 10000.0
                img = np.clip(img / divisor, 0, 1) * 255
                img = img.astype(np.uint8)
            else:
                # keep raw values but ensure they fit in 8-bit for saving
                # if values are larger than 255, scale down linearly preserving range
                img_min = img.min()
                img_max = img.max()
                if img_max == img_min:
                    img8 = np.clip(img, 0, 255).astype(np.uint8)
                else:
                    # scale band-wise to 0-255
                    img_scaled = (img - img_min) / (img_max - img_min) * 255.0
                    img8 = np.clip(img_scaled, 0, 255).astype(np.uint8)
                img = img8

        elif satellite == "sentinel_2_nir":
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))

            if np.issubdtype(img.dtype, np.floating):
                p2, p98 = np.percentile(img, (2, 98))
                img = np.clip((img - p2) / (p98 - p2), 0, 1) * 255
            else:
                divisor = 10000.0 / brightness_factor
                img = np.clip(img / divisor, 0, 1) * 255
            img = img.astype(np.uint8)

        elif satellite == "sentinel_1":
            img = src.read(1)
            p2, p98 = np.percentile(img, (2, 98))
            img = np.clip((img - p2) * (255.0 / (p98 - p2)), 0, 255)
            img = img.astype(np.uint8)
            img = np.stack([img]*3, axis=-1)

        elif satellite == "capella":
            # Capella radar is single-band SAR (float or int). Read first band.
            img = src.read(1)

            # If image is all zeros, keep behavior consistent with other branches
            if np.all(img == 0):
                img = np.zeros((src.height, src.width), dtype=np.uint8)
                img = np.stack([img]*3, axis=-1)
            else:
                # Use percentile stretch for floats or noisy data to reduce speckle impact
                try:
                    p2, p98 = np.percentile(img, (2, 98))
                    if p98 > p2:
                        img_stretched = (img - p2) * (255.0 / (p98 - p2))
                    else:
                        img_stretched = img - p2
                except Exception:
                    # Fallback to simple min-max scaling
                    img_min = img.min()
                    img_max = img.max()
                    if img_max > img_min:
                        img_stretched = (img - img_min) / (img_max - img_min) * 255.0
                    else:
                        img_stretched = np.clip(img, 0, 255)

                img8 = np.clip(img_stretched, 0, 255).astype(np.uint8)
                # Make 3-channel RGB by stacking the single band
                img = np.stack([img8]*3, axis=-1)

        elif satellite == "maxar_imagery":
            # Maxar image is a browse preview (likely 8-bit RGB).
            # assume first three bands are RGB.
            img = src.read([1, 2, 3])
            img = np.transpose(img, (1, 2, 0))
            img = img.astype(np.uint8)
        else:
            raise ValueError(f"Processing not implemented for satellite: {satellite}")

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
