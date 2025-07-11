import numpy as np
import os
import torch
import cv2

from utils import load_config, create_output_dir_and_save_config
from dataset import read_rgb_img, get_patch_info_one_img
from model import SAMRoad
import graph_extraction
import graph_utils
import triage

# from triage import visualize_image_and_graph, rasterize_graph
import pickle
import scipy
import rtree
from collections import defaultdict
import time

from argparse import ArgumentParser


parser = ArgumentParser()
parser.add_argument(
    "--checkpoint", default=None, help="checkpoint of the model to test."
)
parser.add_argument("--config", default=None, help="model config.")
parser.add_argument(
    "--output_dir",
    default=None,
    help="Name of the output dir, if not specified will use timestamp",
)
parser.add_argument("--device", default="cuda", help="device to use for training")

parser.add_argument(
    "--images",
    type=str,
    nargs="+",
    required=True,
    help="List of image paths to process",
)


args = parser.parse_args()


def get_img_paths(root_dir, image_indices):
    img_paths = []

    for ind in image_indices:
        img_paths.append(os.path.join(root_dir, f"region_{ind}_sat.png"))

    return img_paths


def crop_img_patch(img, x0, y0, x1, y1):
    return img[y0:y1, x0:x1, :]


def get_batch_img_patches(img, batch_patch_info):
    patches = []
    # The model expects a fixed patch size, likely defined in the config.
    # We'll assume it's config.PATCH_SIZE for both height and width.
    target_size = config.PATCH_SIZE 

    for _, (x0, y0), (x1, y1) in batch_patch_info:
        patch = crop_img_patch(img, x0, y0, x1, y1)
        
        # --- START OF FIX ---
        h, w, c = patch.shape
        
        # Check if the cropped patch is smaller than the target size
        if h < target_size or w < target_size:
            pad_h = target_size - h
            pad_w = target_size - w
            
            # Pad the patch with zeros to match the target size.
            # The padding is applied to the bottom and right edges.
            patch = np.pad(patch, ((0, pad_h), (0, pad_w), (0, 0)), mode='constant', constant_values=0)
        # --- END OF FIX ---
            
        patches.append(torch.tensor(patch, dtype=torch.float32))
        
    batch = torch.stack(patches, 0).contiguous()
    return batch


def infer_one_img(net, img, config):
    # TODO(congrui): centralize these configs
    img_h, img_w = img.shape[0], img.shape[1]

    batch_size = config.INFER_BATCH_SIZE
    # list of (i, (x_begin, y_begin), (x_end, y_end))
    all_patch_info = get_patch_info_one_img(
        0,
        (img_h, img_w), # Pass a tuple of (height, width)
        config.SAMPLE_MARGIN,
        config.PATCH_SIZE,
        config.INFER_PATCHES_PER_EDGE,
    )
    patch_num = len(all_patch_info)
    batch_num = (
        patch_num // batch_size
        if patch_num % batch_size == 0
        else patch_num // batch_size + 1
    )

    # [IMG_H, IMG_W]
    fused_keypoint_mask = torch.zeros(img.shape[0:2], dtype=torch.float32).to(
        args.device, non_blocking=False
    )
    fused_road_mask = torch.zeros(img.shape[0:2], dtype=torch.float32).to(
        args.device, non_blocking=False
    )
    pixel_counter = torch.zeros(img.shape[0:2], dtype=torch.float32).to(
        args.device, non_blocking=False
    )

    # stores img embeddings for toponet
    # list of [B, D, h, w], len=batch_num
    img_features = list()

    for batch_index in range(batch_num):
        offset = batch_index * batch_size
        batch_patch_info = all_patch_info[offset : offset + batch_size]
        # tensor [B, H, W, C]
        batch_img_patches = get_batch_img_patches(img, batch_patch_info)

        with torch.no_grad():
            batch_img_patches = batch_img_patches.to(args.device, non_blocking=False)
            # [B, H, W, 2]
            mask_scores, patch_img_features = net.infer_masks_and_img_features(
                batch_img_patches
            )
            img_features.append(patch_img_features)
            
        # --- START OF FIX ---
        # Aggregate masks by cropping the output to the correct size
        for patch_index, patch_info in enumerate(batch_patch_info):
            _, (x0, y0), (x1, y1) = patch_info

            # --- START OF DEFINITIVE FIX ---

            # Define the destination slice for the canvas
            keypoint_dest_slice = fused_keypoint_mask[y0:y1, x0:x1]

            # Get the TRUE height and width from the destination slice itself.
            # This automatically handles edge cases where the slice is smaller than the patch.
            h, w = keypoint_dest_slice.shape

            # Get the full output patches from the model
            full_keypoint_patch = mask_scores[patch_index, :, :, 0]
            full_road_patch = mask_scores[patch_index, :, :, 1]
            
            # Crop the model's output to the true dimensions of the destination
            keypoint_patch_cropped = full_keypoint_patch[:h, :w]
            road_patch_cropped = full_road_patch[:h, :w]
            
            # Now, the dimensions are guaranteed to match
            keypoint_dest_slice += keypoint_patch_cropped
            fused_road_mask[y0:y1, x0:x1] += road_patch_cropped

            # The pixel counter must also use the correct, cropped dimensions
            pixel_counter[y0:y1, x0:x1] += torch.ones(
                (h, w), dtype=torch.float32, device=args.device
            )

    fused_keypoint_mask /= pixel_counter
    fused_road_mask /= pixel_counter
    # range 0-1 -> 0-255
    fused_keypoint_mask = (fused_keypoint_mask * 255).to(torch.uint8).cpu().numpy()
    fused_road_mask = (fused_road_mask * 255).to(torch.uint8).cpu().numpy()

    # ## Astar graph extraction
    # pred_graph = graph_extraction.extract_graph_astar(fused_keypoint_mask, fused_road_mask, config)
    # # Doing this conversion to reuse copied code
    # pred_nodes, pred_edges = graph_utils.convert_from_nx(pred_graph)
    # return pred_nodes, pred_edges, fused_keypoint_mask, fused_road_mask
    # ## Astar graph extraction

    ## Extract sample points from masks
    graph_points = graph_extraction.extract_graph_points(
        fused_keypoint_mask, fused_road_mask, config
    )
    if graph_points.shape[0] == 0:
        return (
            graph_points,
            np.zeros((0, 2), dtype=np.int32),
            fused_keypoint_mask,
            fused_road_mask,
        )

    # for box query
    graph_rtree = rtree.index.Index()
    for i, v in enumerate(graph_points):
        x, y = v
        # hack to insert single points
        graph_rtree.insert(i, (x, y, x, y))

    ## Pass 2: infer toponet to predict topology of points from stored img features
    edge_scores = defaultdict(float)
    edge_counts = defaultdict(float)
    for batch_index in range(batch_num):
        offset = batch_index * batch_size
        batch_patch_info = all_patch_info[offset : offset + batch_size]

        topo_data = {
            "points": [],
            "pairs": [],
            "valid": [],
        }
        idx_maps = []

        # prepares pairs queries
        for patch_info in batch_patch_info:
            _, (x0, y0), (x1, y1) = patch_info
            patch_point_indices = list(graph_rtree.intersection((x0, y0, x1, y1)))
            idx_patch2all = {
                patch_idx: all_idx
                for patch_idx, all_idx in enumerate(patch_point_indices)
            }
            patch_point_num = len(patch_point_indices)
            # normalize into patch
            patch_points = graph_points[patch_point_indices, :] - np.array(
                [[x0, y0]], dtype=graph_points.dtype
            )
            # for knn and circle query
            patch_kdtree = scipy.spatial.KDTree(patch_points)

            # k+1 because the nearest one is always self
            # idx is to the patch subgraph
            knn_d, knn_idx = patch_kdtree.query(
                patch_points,
                k=config.MAX_NEIGHBOR_QUERIES + 1,
                distance_upper_bound=config.NEIGHBOR_RADIUS,
            )
            # [patch_point_num, n_nbr]
            knn_idx = knn_idx[:, 1:]  # removes self
            # [patch_point_num, n_nbr] idx is to the patch subgraph
            src_idx = np.tile(
                np.arange(patch_point_num)[:, np.newaxis],
                (1, config.MAX_NEIGHBOR_QUERIES),
            )
            valid = knn_idx < patch_point_num
            tgt_idx = np.where(valid, knn_idx, src_idx)
            # [patch_point_num, n_nbr, 2]
            pairs = np.stack([src_idx, tgt_idx], axis=-1)

            topo_data["points"].append(patch_points)
            topo_data["pairs"].append(pairs)
            topo_data["valid"].append(valid)
            idx_maps.append(idx_patch2all)

        # collate
        collated = {}
        for key, x_list in topo_data.items():
            length = max([x.shape[0] for x in x_list])
            collated[key] = np.stack(
                [
                    np.pad(
                        x, [(0, length - x.shape[0])] + [(0, 0)] * (len(x.shape) - 1)
                    )
                    for x in x_list
                ],
                axis=0,
            )

        # skips this batch if there's no points
        if collated["points"].shape[1] == 0:
            continue

        # infer toponet
        # [B, D, h, w]
        batch_features = img_features[batch_index]
        # [B, N_sample, N_pair, 2]
        batch_points = torch.tensor(collated["points"], device=args.device)
        batch_pairs = torch.tensor(collated["pairs"], device=args.device)
        batch_valid = torch.tensor(collated["valid"], device=args.device)

        with torch.no_grad():
            # [B, N_samples, N_pairs, 1]
            topo_scores = net.infer_toponet(
                batch_features, batch_points, batch_pairs, batch_valid
            )

        # all-invalid (padded, no neighbors) queries returns nan scores
        # [B, N_samples, N_pairs]
        topo_scores = (
            torch.where(torch.isnan(topo_scores), -100.0, topo_scores)
            .squeeze(-1)
            .cpu()
            .numpy()
        )

        # aggregate edge scores
        batch_size, n_samples, n_pairs = topo_scores.shape
        for bi in range(batch_size):
            for si in range(n_samples):
                for pi in range(n_pairs):
                    if not collated["valid"][bi, si, pi]:
                        continue
                    # idx to the full graph
                    src_idx_patch, tgt_idx_patch = collated["pairs"][bi, si, pi, :]
                    src_idx_all, tgt_idx_all = (
                        idx_maps[bi][src_idx_patch],
                        idx_maps[bi][tgt_idx_patch],
                    )
                    edge_score = topo_scores[bi, si, pi]
                    assert 0.0 <= edge_score <= 1.0
                    edge_scores[(src_idx_all, tgt_idx_all)] += edge_score
                    edge_counts[(src_idx_all, tgt_idx_all)] += 1.0

    # avg edge scores and filter
    pred_edges = []
    for edge, score_sum in edge_scores.items():
        score = score_sum / edge_counts[edge]
        if score > config.TOPO_THRESHOLD:
            pred_edges.append(edge)
    pred_edges = np.array(pred_edges).reshape(-1, 2)
    pred_nodes = graph_points[:, ::-1]  # to rc

    return pred_nodes, pred_edges, fused_keypoint_mask, fused_road_mask


if __name__ == "__main__":
    config = load_config(args.config)

    # Builds eval model
    device = torch.device("cuda") if args.device == "cuda" else torch.device("cpu")
    # Good when model architecture/input shape are fixed.
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.enabled = True
    net = SAMRoad(config)

    # load checkpoint
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    print(f"##### Loading Trained CKPT {args.checkpoint} #####")
    net.load_state_dict(checkpoint["state_dict"], strict=True)
    net.eval()
    net.to(device)

    custom_image_paths = args.images

    output_dir_prefix = "./save/infer_"
    if args.output_dir:
        output_dir = create_output_dir_and_save_config(
            output_dir_prefix, config, specified_dir=f"./save/{args.output_dir}"
        )
    else:
        output_dir = create_output_dir_and_save_config(output_dir_prefix, config)

    total_inference_seconds = 0.0

    for img_id, img_path in enumerate(custom_image_paths):
        print(f"Processing {img_path}")
        img = read_rgb_img(img_path)
        start_seconds = time.time()
        # coords in (r, c)
        pred_nodes, pred_edges, itsc_mask, road_mask = infer_one_img(net, img, config)
        end_seconds = time.time()
        total_inference_seconds += end_seconds - start_seconds

        # RGB already
        viz_img = np.copy(img)
        img_size = viz_img.shape[0]

        # visualizes fused masks
        mask_save_dir = os.path.join(output_dir, "mask")
        if not os.path.exists(mask_save_dir):
            os.makedirs(mask_save_dir)
        cv2.imwrite(os.path.join(mask_save_dir, f"{img_id}_road.png"), road_mask)
        cv2.imwrite(os.path.join(mask_save_dir, f"{img_id}_itsc.png"), itsc_mask)

        # Visualizes merged large map
        viz_save_dir = os.path.join(output_dir, "viz")
        if not os.path.exists(viz_save_dir):
            os.makedirs(viz_save_dir)
        viz_img = triage.visualize_image_and_graph(
            viz_img, pred_nodes / img_size, pred_edges, viz_img.shape[0]
        )
        cv2.imwrite(os.path.join(viz_save_dir, f"{img_id}.png"), viz_img)

        # Saves the large map
        # if config.DATASET == "spacenet":
        #     # r, c -> ???
        #     pred_nodes = np.stack([400 - pred_nodes[:, 0], pred_nodes[:, 1]], axis=1)
        large_map_sat2graph_format = graph_utils.convert_to_sat2graph_format(
            pred_nodes, pred_edges
        )
        graph_save_dir = os.path.join(output_dir, "graph")
        if not os.path.exists(graph_save_dir):
            os.makedirs(graph_save_dir)
        graph_save_path = os.path.join(graph_save_dir, f"{img_id}.p")
        with open(graph_save_path, "wb") as file:
            pickle.dump(large_map_sat2graph_format, file)

        print(f"Done for {img_id}.")

    # log inference time
    time_txt = (
        f"Inference completed for {args.config} in {total_inference_seconds} seconds."
    )
    print(time_txt)
    with open(os.path.join(output_dir, "inference_time.txt"), "w") as f:
        f.write(time_txt)
