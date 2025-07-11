import numpy as np
import torch
from torch.utils.data import Dataset
import cv2
import math
import graph_utils
import rtree
import scipy
import pickle
import os
import addict
import json
import functools


def read_rgb_img(path):
    bgr = cv2.imread(path)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return rgb


def cityscale_data_partition():
    # dataset partition
    indrange_train = []
    indrange_test = []
    indrange_validation = []

    for x in range(180):
        if x % 10 < 8:
            indrange_train.append(x)

        if x % 10 == 9:
            indrange_test.append(x)

        if x % 20 == 18:
            indrange_validation.append(x)

        if x % 20 == 8:
            indrange_test.append(x)
    return indrange_train, indrange_validation, indrange_test


def spacenet_data_partition():
    # dataset partition
    with open("./spacenet/data_split.json", "r") as jf:
        data_list = json.load(jf)
    train_list = data_list["train"]
    val_list = data_list["validation"]
    test_list = data_list["test"]
    return train_list, val_list, test_list


def south_uk_data_partition():
    # dataset partition
    with open("./south_uk/data_split_clean.json", "r") as jf:
        data_list = json.load(jf)
    train_list = data_list["train"]
    val_list = data_list["validation"]
    test_list = data_list["test"]
    return train_list, val_list, test_list


def get_patch_info_one_img(
    image_index, image_dims, sample_margin, patch_size, patches_per_edge
):
    # --- START OF FIX ---
    # Unpack the height and width from the image_dims tuple
    img_h, img_w = image_dims
    patch_info = []
    sample_min = sample_margin

    # Calculate separate sample points for the X-axis (width)
    sample_max_x = img_w - (patch_size + sample_margin)
    eval_samples_x = np.linspace(start=sample_min, stop=sample_max_x, num=patches_per_edge)
    eval_samples_x = [round(x) for x in eval_samples_x]

    # Calculate separate sample points for the Y-axis (height)
    sample_max_y = img_h - (patch_size + sample_margin)
    eval_samples_y = np.linspace(start=sample_min, stop=sample_max_y, num=patches_per_edge)
    eval_samples_y = [round(y) for y in eval_samples_y]

    # Iterate over the Y and X samples separately to build a rectangular grid
    for y in eval_samples_y:
        for x in eval_samples_x:
            patch_info.append((image_index, (x, y), (x + patch_size, y + patch_size)))
    # --- END OF FIX ---
    
    return patch_info


class GraphLabelGenerator:
    def __init__(self, config, full_graph, coord_transform):
        self.config = config
        # full_graph: sat2graph format
        # coord_transform: lambda, [N, 2] array -> [N, 2] array
        # convert to igraph for high performance
        self.full_graph_origin = graph_utils.igraph_from_adj_dict(
            full_graph, coord_transform
        )
        # find crossover points, we'll avoid predicting these as keypoints
        self.crossover_points = graph_utils.find_crossover_points(
            self.full_graph_origin
        )
        # subdivide version
        self.subdivide_resolution = 4
        self.full_graph_subdivide = graph_utils.subdivide_graph(
            self.full_graph_origin, self.subdivide_resolution
        )
        self.subdivide_points = np.array(self.full_graph_subdivide.vs["point"])
        # pre-build spatial index
        # rtree for box queries
        self.graph_rtee = rtree.index.Index()
        for i, v in enumerate(self.subdivide_points):
            x, y = v
            # hack to insert single points
            self.graph_rtee.insert(i, (x, y, x, y))
        # kdtree for spherical query
        self.graph_kdtree = scipy.spatial.KDTree(self.subdivide_points)

        # pre-exclude points near crossover points
        crossover_exclude_radius = 4
        exclude_indices = set()
        for p in self.crossover_points:
            nearby_indices = self.graph_kdtree.query_ball_point(
                p, crossover_exclude_radius
            )
            exclude_indices.update(nearby_indices)
        self.exclude_indices = exclude_indices

        # Find intersection points, these will always be kept in nms
        itsc_indices = set()
        point_num = len(self.full_graph_subdivide.vs)
        for i in range(point_num):
            if self.full_graph_subdivide.degree(i) != 2:
                itsc_indices.add(i)
        self.nms_score_override = np.zeros((point_num,), dtype=np.float32)
        self.nms_score_override[np.array(list(itsc_indices))] = (
            2.0  # itsc points will always be kept
        )

        # Points near crossover and intersections are interesting.
        # they will be more frequently sampled
        interesting_indices = set()
        interesting_radius = 32
        # near itsc
        for i in itsc_indices:
            p = self.subdivide_points[i]
            nearby_indices = self.graph_kdtree.query_ball_point(p, interesting_radius)
            interesting_indices.update(nearby_indices)
        for p in self.crossover_points:
            nearby_indices = self.graph_kdtree.query_ball_point(
                np.array(p), interesting_radius
            )
            interesting_indices.update(nearby_indices)
        self.sample_weights = np.full((point_num,), 0.1, dtype=np.float32)
        self.sample_weights[list(interesting_indices)] = 0.9

    def sample_patch(self, patch, rot_index=0):
        (x0, y0), (x1, y1) = patch
        query_box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        patch_indices_all = set(self.graph_rtee.intersection(query_box))
        patch_indices = patch_indices_all - self.exclude_indices

        # Use NMS to downsample, params shall resemble inference time
        patch_indices = np.array(list(patch_indices))
        if len(patch_indices) == 0:
            # print("==== Patch is empty ====")
            # this shall be rare, but if no points in side the patch, return null stuff
            sample_num = self.config.TOPO_SAMPLE_NUM
            max_nbr_queries = self.config.MAX_NEIGHBOR_QUERIES
            fake_points = np.array([[0.0, 0.0]], dtype=np.float32)
            fake_sample = (
                [[0, 0]] * max_nbr_queries,
                [False] * max_nbr_queries,
                [False] * max_nbr_queries,
            )
            return fake_points, [fake_sample] * sample_num

        patch_points = self.subdivide_points[patch_indices, :]

        # random scores to emulate different random configurations that all share a
        # similar spacing between sampled points
        # raise scores for intersction points so they are always kept
        nms_scores = np.random.uniform(low=0.9, high=1.0, size=patch_indices.shape[0])
        nms_score_override = self.nms_score_override[patch_indices]
        nms_scores = np.maximum(nms_scores, nms_score_override)
        nms_radius = self.config.ROAD_NMS_RADIUS

        # kept_indces are into the patch_points array
        nmsed_points, kept_indices = graph_utils.nms_points(
            patch_points, nms_scores, radius=nms_radius, return_indices=True
        )
        # now this is into the subdivide graph
        nmsed_indices = patch_indices[kept_indices]
        nmsed_point_num = nmsed_points.shape[0]

        sample_num = self.config.TOPO_SAMPLE_NUM  # has to be greater than 1
        sample_weights = self.sample_weights[nmsed_indices]
        # indices into the nmsed points in the patch
        sample_indices_in_nmsed = np.random.choice(
            np.arange(start=0, stop=nmsed_points.shape[0], dtype=np.int32),
            size=sample_num,
            replace=True,
            p=sample_weights / np.sum(sample_weights),
        )
        # indices into the subdivided graph
        sample_indices = nmsed_indices[sample_indices_in_nmsed]

        radius = self.config.NEIGHBOR_RADIUS
        max_nbr_queries = self.config.MAX_NEIGHBOR_QUERIES  # has to be greater than 1
        nmsed_kdtree = scipy.spatial.KDTree(nmsed_points)
        sampled_points = self.subdivide_points[sample_indices, :]
        # [n_sample, n_nbr]
        # k+1 because the nearest one is always self
        knn_d, knn_idx = nmsed_kdtree.query(
            sampled_points, k=max_nbr_queries + 1, distance_upper_bound=radius
        )

        samples = []

        for i in range(sample_num):
            source_node = sample_indices[i]
            valid_nbr_indices = knn_idx[i, knn_idx[i, :] < nmsed_point_num]
            valid_nbr_indices = valid_nbr_indices[
                1:
            ]  # the nearest one is self so remove
            target_nodes = [nmsed_indices[ni] for ni in valid_nbr_indices]

            ### BFS to find immediate neighbors on graph
            reached_nodes = graph_utils.bfs_with_conditions(
                self.full_graph_subdivide,
                source_node,
                set(target_nodes),
                radius // self.subdivide_resolution,
            )
            shall_connect = [t in reached_nodes for t in target_nodes]
            ###

            pairs = []
            valid = []
            source_nmsed_idx = sample_indices_in_nmsed[i]
            for target_nmsed_idx in valid_nbr_indices:
                pairs.append((source_nmsed_idx, target_nmsed_idx))
                valid.append(True)

            # zero-pad
            for i in range(len(pairs), max_nbr_queries):
                pairs.append((source_nmsed_idx, source_nmsed_idx))
                shall_connect.append(False)
                valid.append(False)

            samples.append((pairs, shall_connect, valid))

        # Transform points
        # [N, 2]
        nmsed_points -= np.array([x0, y0])[np.newaxis, :]
        # homo for rot
        # [N, 3]
        nmsed_points = np.concatenate(
            [nmsed_points, np.ones((nmsed_point_num, 1), dtype=nmsed_points.dtype)],
            axis=1,
        )
        trans = np.array(
            [
                [1, 0, -0.5 * self.config.PATCH_SIZE],
                [0, 1, -0.5 * self.config.PATCH_SIZE],
                [0, 0, 1],
            ],
            dtype=np.float32,
        )
        # ccw 90 deg in img (x, y)
        rot = np.array(
            [
                [0, 1, 0],
                [-1, 0, 0],
                [0, 0, 1],
            ],
            dtype=np.float32,
        )
        nmsed_points = (
            nmsed_points
            @ trans.T
            @ np.linalg.matrix_power(rot.T, rot_index)
            @ np.linalg.inv(trans.T)
        )
        nmsed_points = nmsed_points[:, :2]

        # Add noise
        noise_scale = 1.0  # pixels
        nmsed_points += np.random.normal(0.0, noise_scale, size=nmsed_points.shape)

        return nmsed_points, samples


def test_graph_label_generator():
    if not os.path.exists("debug"):
        os.mkdir("debug")

    dataset = "spacenet"
    if dataset == "cityscale":
        rgb_path = "./cityscale/20cities/region_166_sat.png"
        # Load GT Graph
        gt_graph = pickle.load(
            open("./cityscale/20cities/region_166_refine_gt_graph.p", "rb")
        )

        def coord_transform(v):
            return v[:, ::-1]
    elif dataset == "spacenet":
        rgb_path = "spacenet/RGB_1.0_meter/AOI_2_Vegas_210__rgb.png"
        # Load GT Graph
        gt_graph = pickle.load(
            open("spacenet/RGB_1.0_meter/AOI_2_Vegas_210__gt_graph.p", "rb")
        )
        # gt_graph = pickle.load(open(f"spacenet/RGB_1.0_meter/AOI_4_Shanghai_1061__gt_graph_dense_spacenet.p",'rb'))

        def coord_transform(v):
            return np.stack([v[:, 1], 400 - v[:, 0]], axis=1)

        # coord_transform = lambda v : v[:, ::-1]
    rgb = read_rgb_img(rgb_path)
    config = addict.Dict()
    config.PATCH_SIZE = 256
    config.ROAD_NMS_RADIUS = 16
    config.TOPO_SAMPLE_NUM = 4
    config.NEIGHBOR_RADIUS = 64
    config.MAX_NEIGHBOR_QUERIES = 16
    gen = GraphLabelGenerator(config, gt_graph, coord_transform)
    patch = ((x0, y0), (x1, y1)) = (
        (64, 64),
        (64 + config.PATCH_SIZE, 64 + config.PATCH_SIZE),
    )
    test_num = 64
    for i in range(test_num):
        rot_index = np.random.randint(0, 4)
        points, samples = gen.sample_patch(patch, rot_index=rot_index)
        rgb_patch = rgb[y0:y1, x0:x1, ::-1].copy()
        rgb_patch = np.rot90(rgb_patch, rot_index, (0, 1)).copy()
        for pairs, shall_connect, valid in samples:
            color = tuple(int(c) for c in np.random.randint(0, 256, size=3))

            for (src, tgt), connected, is_valid in zip(pairs, shall_connect, valid):
                if not is_valid:
                    continue
                p0, p1 = points[src], points[tgt]
                cv2.circle(rgb_patch, p0.astype(np.int32), 4, color, -1)
                cv2.circle(rgb_patch, p1.astype(np.int32), 2, color, -1)
                if connected:
                    cv2.line(
                        rgb_patch,
                        (int(p0[0]), int(p0[1])),
                        (int(p1[0]), int(p1[1])),
                        (255, 255, 255),
                        1,
                    )
        cv2.imwrite(f"debug/viz_{i}.png", rgb_patch)


def graph_collate_fn(batch):
    keys = batch[0].keys()
    collated = {}
    for key in keys:
        if key == "graph_points":
            tensors = [item[key] for item in batch]
            max_point_num = max([x.shape[0] for x in tensors])
            padded = []
            for x in tensors:
                pad_num = max_point_num - x.shape[0]
                padded_x = torch.concat([x, torch.zeros(pad_num, 2)], dim=0)
                padded.append(padded_x)
            collated[key] = torch.stack(padded, dim=0)
        else:
            collated[key] = torch.stack([item[key] for item in batch], dim=0)
    return collated


class SatMapDataset(Dataset):
    def __init__(self, config, is_train, dev_run=False):
        self.config = config
        self.is_train = is_train

        assert self.config.DATASET in {"cityscale", "spacenet", "south_uk"}

        if self.config.DATASET == "cityscale":
            self.IMAGE_SIZE = 2048
            self.SAMPLE_MARGIN = 64
            rgb_pattern = "./cityscale/20cities/region_{}_sat.png"
            keypoint_mask_pattern = "./cityscale/processed/keypoint_mask_{}.png"
            road_mask_pattern = "./cityscale/processed/road_mask_{}.png"
            gt_graph_pattern = "./cityscale/20cities/region_{}_refine_gt_graph.p"
            train, val, test = cityscale_data_partition()
            self.coord_transform = lambda v: v[:, ::-1]

        elif self.config.DATASET == "spacenet":
            self.IMAGE_SIZE = 400
            self.SAMPLE_MARGIN = 0
            rgb_pattern = "./spacenet/RGB_1.0_meter/{}__rgb.png"
            keypoint_mask_pattern = "./spacenet/processed/keypoint_mask_{}.png"
            road_mask_pattern = "./spacenet/processed/road_mask_{}.png"
            gt_graph_pattern = "./spacenet/RGB_1.0_meter/{}__gt_graph.p"
            train, val, test = spacenet_data_partition()
            self.coord_transform = lambda v: np.stack([v[:, 1], 400 - v[:, 0]], axis=1)

        elif self.config.DATASET == "south_uk":
            # --- UPDATED SOUTH_UK CONFIGURATION ---
            self.IMAGE_SIZE = 512  # Please verify this is the correct dimension
            self.SAMPLE_MARGIN = 0

            # Paths for your renamed satellite images and graphs
            rgb_pattern = "./south_uk/renamed_files/{}.png"
            gt_graph_pattern = "./south_uk/renamed_files/{}.p"

            # Paths for your processed masks, as you provided
            keypoint_mask_pattern = "./south_uk/processed/keypoint_mask_{}.png"
            road_mask_pattern = "./south_uk/processed/road_mask_{}.png"

            train, val, test = south_uk_data_partition()
            self.coord_transform = lambda v: v  # Start with identity, adjust if needed

        tile_indices = (train + val) if self.is_train else test
        if dev_run:
            tile_indices = tile_indices[:4]

        # Store only the file paths, NOT the data itself (Lazy Loading)
        self.rgb_paths = [rgb_pattern.format(idx) for idx in tile_indices]
        self.gt_graph_paths = [gt_graph_pattern.format(idx) for idx in tile_indices]
        self.keypoint_mask_paths = [
            keypoint_mask_pattern.format(idx) for idx in tile_indices
        ]
        self.road_mask_paths = [road_mask_pattern.format(idx) for idx in tile_indices]

        # Caching dictionary to avoid re-computing heavy GraphLabelGenerators
        self.graph_label_generator_cache = {}

        if not self.is_train:
            self.eval_patches = []
            eval_patches_per_edge = math.ceil(
                (self.IMAGE_SIZE - 2 * self.SAMPLE_MARGIN) / self.config.PATCH_SIZE
            )
            for i in range(len(tile_indices)):
                self.eval_patches += get_patch_info_one_img(
                    i,
                    self.IMAGE_SIZE,
                    self.SAMPLE_MARGIN,
                    self.config.PATCH_SIZE,
                    eval_patches_per_edge,
                )

    @functools.lru_cache(maxsize=256)
    def _get_generator(self, img_idx):
        """
        A cached method to create and return a GraphLabelGenerator.
        This function's results will be automatically cached.
        """
        # This is the same logic that was previously in __getitem__
        gt_graph_adj = pickle.load(open(self.gt_graph_paths[img_idx], "rb"))

        # We still check for empty graphs, but this should be rare now
        if len(gt_graph_adj) == 0:
            return None

        return GraphLabelGenerator(self.config, gt_graph_adj, self.coord_transform)

    def __len__(self):
        if self.is_train:
            # This logic should be restored from your original code to match epoch sizes
            if self.config.DATASET == "cityscale":
                return max(1, int(self.IMAGE_SIZE / self.config.PATCH_SIZE)) ** 2 * 2500
            elif self.config.DATASET == "spacenet":
                return 84667
            elif self.config.DATASET == "south_uk":
                # Set a reasonable epoch size for your new dataset
                return len(self.rgb_paths) * 10
        else:
            return len(self.eval_patches)

    def __getitem__(self, idx):
        if self.is_train:
            img_idx = np.random.randint(low=0, high=len(self.rgb_paths))
        else:
            img_idx, (begin_x, begin_y), (end_x, end_y) = self.eval_patches[idx]

        graph_label_generator = self._get_generator(img_idx)

        if graph_label_generator is None:
            return self.__getitem__(np.random.randint(0, len(self) - 1))

        # Load the single image and masks needed for this item
        rgb_image = read_rgb_img(self.rgb_paths[img_idx])
        keypoint_mask = cv2.imread(
            self.keypoint_mask_paths[img_idx], cv2.IMREAD_GRAYSCALE
        )
        road_mask = cv2.imread(self.road_mask_paths[img_idx], cv2.IMREAD_GRAYSCALE)

        if self.is_train:
            sample_max = self.IMAGE_SIZE - (self.config.PATCH_SIZE + self.SAMPLE_MARGIN)
            begin_x = np.random.randint(low=self.SAMPLE_MARGIN, high=sample_max + 1)
            begin_y = np.random.randint(low=self.SAMPLE_MARGIN, high=sample_max + 1)
            end_x, end_y = (
                begin_x + self.config.PATCH_SIZE,
                begin_y + self.config.PATCH_SIZE,
            )

        # Crop patches
        rgb_patch = rgb_image[begin_y:end_y, begin_x:end_x, :]
        keypoint_mask_patch = keypoint_mask[begin_y:end_y, begin_x:end_x]
        road_mask_patch = road_mask[begin_y:end_y, begin_x:end_x]

        # Augmentation
        rot_index = 0
        if self.is_train:
            rot_index = np.random.randint(0, 4)
            rgb_patch = np.rot90(rgb_patch, rot_index, [0, 1]).copy()
            keypoint_mask_patch = np.rot90(
                keypoint_mask_patch, rot_index, [0, 1]
            ).copy()
            road_mask_patch = np.rot90(road_mask_patch, rot_index, [0, 1]).copy()

        patch_coords = ((begin_x, begin_y), (end_x, end_y))
        graph_points, topo_samples = graph_label_generator.sample_patch(
            patch_coords, rot_index=rot_index
        )

        pairs, connected, valid = zip(*topo_samples)

        return {
            "rgb": torch.tensor(rgb_patch, dtype=torch.float32),
            "keypoint_mask": torch.tensor(keypoint_mask_patch, dtype=torch.float32)
            / 255.0,
            "road_mask": torch.tensor(road_mask_patch, dtype=torch.float32) / 255.0,
            "graph_points": torch.tensor(graph_points, dtype=torch.float32),
            "pairs": torch.tensor(pairs, dtype=torch.int32),
            "connected": torch.tensor(connected, dtype=torch.bool),
            "valid": torch.tensor(valid, dtype=torch.bool),
        }


if __name__ == "__main__":
    test_graph_label_generator()
