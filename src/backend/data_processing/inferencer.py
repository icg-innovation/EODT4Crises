import numpy as np
import os
import torch
import cv2
import rasterio
import json
import logging
from rasterio.warp import transform_bounds

from utils import load_config, create_output_dir_and_save_config
from model import SAMRoad
import graph_extraction
import graph_utils

import pickle
import time
from argparse import ArgumentParser

import math
from tqdm import tqdm
from rasterio.windows import from_bounds

parser = ArgumentParser()
parser.add_argument("--checkpoint", default=None, help="checkpoint of the model to test.")
parser.add_argument("--config", default=None, help="model config.")
parser.add_argument("--output_dir", default=None, help="Name of the output dir, if not specified will use timestamp")
parser.add_argument("--device", default="cuda", help="device to use for training")
parser.add_argument("--bbox", type=float, nargs=4, default=None, help="Bounding box to crop in min_lon min_lat max_lon max_lat format.")
parser.add_argument("--images", type=str, nargs="+", required=True, help="List of image paths to process")

args = parser.parse_args()
logging.info("Parsed arguments: %s", args)

def _gen_positions(L, win, stride):
    if L <= win:
        return [0]
    pos = list(range(0, max(L - win, 0) + 1, stride))
    last = L - win
    if not pos or pos[-1] != last:
        pos.append(last)
    return pos

def _pad_to_size(patch, target_h, target_w):
    h, w = patch.shape[:2]
    pad_bottom = max(0, target_h - h)
    pad_right  = max(0, target_w - w)
    if pad_bottom == 0 and pad_right == 0:
        return patch
    return cv2.copyMakeBorder(patch, 0, pad_bottom, 0, pad_right, borderType=cv2.BORDER_REPLICATE)

def infer_one_img(net, img_path, config, bbox=None, target_resolution_m=10.0, overlap_hr=64):
    TILE_SIZE = config.PATCH_SIZE
    BATCH_SIZE = config.INFER_BATCH_SIZE

    with rasterio.open(img_path) as src:
        window = None
        if bbox:
            try:
                # Bbox is geographic (lon/lat, EPSG:4326) from the frontend
                min_lon, min_lat, max_lon, max_lat = map(float, bbox)
                if src.crs is None:
                    raise ValueError("Source image has no CRS — cannot apply bbox in geographic coords.")

                # **FIX:** Transform the geographic bbox to the raster's native coordinate system
                left, bottom, right, top = transform_bounds(
                    'EPSG:4326',
                    src.crs,
                    min_lon,
                    min_lat,
                    max_lon,
                    max_lat
                )

                # Now use the correctly projected coordinates to get the pixel window
                window = from_bounds(left, bottom, right, top, src.transform)

            except Exception as e:
                logging.warning("Could not apply bbox; using full image. Error: %s", e)

        transform_lr = src.window_transform(window) if window else src.transform
        img_lr = src.read([1, 2, 3], window=window).transpose(1, 2, 0)

        divisor = 10000.0
        img_lr = np.clip(img_lr / divisor, 0, 1) * 255
        img_lr = img_lr.astype(np.uint8)

    original_resolution = abs(transform_lr.a)
    scale_factor = original_resolution / float(target_resolution_m)
    if scale_factor < 1.0:
        logging.info("Input resolution (%.3f m/px) is already finer than target; setting scale_factor=1.0.", original_resolution)
        scale_factor = 1.0

    H_lr, W_lr = img_lr.shape[:2]
    H_hr = int(round(H_lr * scale_factor))
    W_hr = int(round(W_lr * scale_factor))

    tile_size_lr = max(1, int(math.ceil(TILE_SIZE / scale_factor)))
    stride_lr    = max(1, int(math.ceil((TILE_SIZE - overlap_hr) / scale_factor)))

    ys = _gen_positions(H_lr, tile_size_lr, stride_lr)
    xs = _gen_positions(W_lr, tile_size_lr, stride_lr)

    fused_keypoint_mask = np.zeros((H_hr, W_hr), dtype=np.float32)
    fused_road_mask = np.zeros((H_hr, W_hr), dtype=np.float32)
    weight_mask     = np.zeros((H_hr, W_hr), dtype=np.float32)
    batch_tiles, batch_paste_xy_hr = [], []

    def flush_batch():
        if not batch_tiles: return
        batch_array = np.stack(batch_tiles, axis=0)
        batch_tensor = torch.from_numpy(batch_array).to(args.device)
        with torch.no_grad():
            mask_scores, _ = net.infer_masks_and_img_features(batch_tensor)
            mask_scores = mask_scores.cpu().numpy()

        keypoint_scores = mask_scores[..., 0]
        road_scores = mask_scores[..., 1]

        for i, (y_hr, x_hr) in enumerate(batch_paste_xy_hr):
            h_valid, w_valid = min(TILE_SIZE, H_hr - y_hr), min(TILE_SIZE, W_hr - x_hr)
            if h_valid > 0 and w_valid > 0:
                fused_keypoint_mask[y_hr:y_hr+h_valid, x_hr:x_hr+w_valid] += keypoint_scores[i, :h_valid, :w_valid]
                fused_road_mask[y_hr:y_hr+h_valid, x_hr:x_hr+w_valid] += road_scores[i, :h_valid, :w_valid]
                weight_mask[y_hr:y_hr+h_valid, x_hr:x_hr+w_valid] += 1.0
        batch_tiles.clear(); batch_paste_xy_hr.clear()

    for y_lr in tqdm(ys, desc="Processing Tiles"):
        for x_lr in xs:
            patch_lr = img_lr[y_lr:min(y_lr+tile_size_lr, H_lr), x_lr:min(x_lr+tile_size_lr, W_lr), :]
            patch_lr_padded = _pad_to_size(patch_lr, tile_size_lr, tile_size_lr)
            patch_hr = cv2.resize(patch_lr_padded, (TILE_SIZE, TILE_SIZE), interpolation=cv2.INTER_CUBIC)
            batch_tiles.append(patch_hr)
            batch_paste_xy_hr.append((int(round(y_lr * scale_factor)), int(round(x_lr * scale_factor))))
            if len(batch_tiles) == BATCH_SIZE: flush_batch()
    flush_batch()

    np.divide(fused_road_mask, weight_mask, out=fused_road_mask, where=weight_mask > 0)
    np.divide(fused_keypoint_mask, weight_mask, out=fused_keypoint_mask, where=weight_mask > 0)

    fused_keypoint_mask_uint8 = (np.clip(fused_keypoint_mask, 0.0, 1.0) * 255).astype(np.uint8)
    fused_road_mask_uint8 = (np.clip(fused_road_mask, 0.0, 1.0) * 255).astype(np.uint8)

    graph = graph_extraction.extract_graph_astar(fused_keypoint_mask_uint8, fused_road_mask_uint8, config)

    if len(graph.nodes()) == 0:
        return np.zeros((0, 2), dtype=np.float32), np.zeros((0, 2), dtype=np.int32), fused_keypoint_mask_uint8, fused_road_mask_uint8, transform_lr

    pred_nodes_hr_xy = np.array([[x, y] for (y, x) in graph.nodes()], dtype=np.float32)
    pred_nodes_lr_xy = pred_nodes_hr_xy / scale_factor

    node_list = list(graph.nodes())
    node_index = {node: idx for idx, node in enumerate(node_list)}
    pred_edges = np.array(
        [(node_index[u], node_index[v]) for u, v in graph.edges()],
        dtype=np.int32
    )

    return pred_nodes_lr_xy, pred_edges, fused_keypoint_mask_uint8, fused_road_mask_uint8, transform_lr


if __name__ == "__main__":
    config = load_config(args.config)
    device = torch.device(args.device)
    torch.backends.cudnn.benchmark = True

    net = SAMRoad(config)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    logging.info("Loading trained checkpoint: %s", args.checkpoint)
    net.load_state_dict(checkpoint["state_dict"], strict=True)
    net.eval().to(device)

    output_dir_prefix = "./save/infer_"
    if args.output_dir:
        output_dir = create_output_dir_and_save_config(output_dir_prefix, config, specified_dir=f"./save/{args.output_dir}")
    else:
        output_dir = create_output_dir_and_save_config(output_dir_prefix, config)

    total_inference_seconds = 0.0

    for img_id, img_path in enumerate(args.images):
        print(f"Processing {img_path}")
        start_seconds = time.time()
        pred_nodes, pred_edges, itsc_mask, road_mask, geo_transform = infer_one_img(net, img_path, config, bbox=args.bbox)
        total_inference_seconds += time.time() - start_seconds

        mask_save_dir = os.path.join(output_dir, "mask")
        os.makedirs(mask_save_dir, exist_ok=True)
        cv2.imwrite(os.path.join(mask_save_dir, f"{img_id}_road.png"), road_mask)
        cv2.imwrite(os.path.join(mask_save_dir, f"{img_id}_itsc.png"), itsc_mask)

        graph_save_dir = os.path.join(output_dir, "graph")
        os.makedirs(graph_save_dir, exist_ok=True)

        large_map_sat2graph_format = graph_utils.convert_to_sat2graph_format(pred_nodes, pred_edges)
        graph_save_path = os.path.join(graph_save_dir, f"{img_id}.p")
        with open(graph_save_path, "wb") as file:
            pickle.dump(large_map_sat2graph_format, file)

        transform_save_path = os.path.join(graph_save_dir, f"{img_id}_transform.json")
        with open(transform_save_path, "w") as f:
            json.dump(geo_transform.to_gdal(), f)

        print(f"Done for {img_id}.")

    time_txt = f"Inference completed in {total_inference_seconds:.2f} seconds."
    print(time_txt)
    with open(os.path.join(output_dir, "inference_time.txt"), "w") as f:
        f.write(time_txt)