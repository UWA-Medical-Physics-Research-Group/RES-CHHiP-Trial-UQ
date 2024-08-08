"""
Functions for hanlding dataset
"""

import tensorflow as tf
from tests.context import PatientScan
from uncertainty.data.mask import get_organ_names, masks_as_array
from uncertainty.data.preprocessing import (
    crop_nd,
    filter_roi,
    find_organ_roi,
    map_interval,
    shift_center,
)
from ..utils.logging import logger_wraps
from ..utils.wrappers import curry
from ..constants import BODY_THRESH, HU_RANGE, ORGAN_MATCHES
from ..models.config import model_config

from typing import Iterable, Optional, TypedDict

import numpy as np
import toolz as tz
import toolz.curried as curried
from volumentations import (
    Compose,
    Rotate,
    ElasticTransform,
    Flip,
    GaussianNoise,
    RandomGamma,
)


PreprocessDataDict = TypedDict(
    "PreprocessDataDict",
    {
        "input_height": int,
        "input_width": int,
        "input_depth": int,
    },
)


@logger_wraps(level="INFO")
@curry
def preprocess_data(
    scan: PatientScan, config: PreprocessDataDict = model_config()
) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """
    Preprocess a PatientScan object into (volume, masks) pairs

    Mask for multiple organs are stacked along the last dimension to have
    shape (height, width, depth, n_organs). Mask is `None` if not all organs are present.
    """
    # use same centroid for both volume and mask
    centroid = np.mean(np.argwhere(scan.volume > BODY_THRESH), axis=0)

    def preprocess_volume(scan: PatientScan) -> np.ndarray:
        return tz.pipe(
            scan.volume,
            shift_center(points=centroid),
            crop_nd(
                new_shape=(
                    config["input_height"],
                    config["input_width"],
                    config["input_depth"],
                ),
                pad=True,
            ),
            lambda vol: np.clip(vol, *HU_RANGE),
            map_interval(HU_RANGE, (0, 1)),
            lambda vol: vol.astype(np.float32),
        )

    def preprocess_mask(scan: PatientScan) -> Optional[np.ndarray]:
        """
        Returns a mask with all organs present, or None if not all organs are present
        """
        names = tz.pipe(
            get_organ_names(scan.masks[""]),
            filter_roi,
            lambda mask_names: [
                find_organ_roi(organ, mask_names) for organ in ORGAN_MATCHES
            ],
            curried.filter(lambda m: m is not None),
            list,
        )

        # If not all organs are present, return None
        if len(names) != len(ORGAN_MATCHES):
            return None

        return tz.pipe(
            scan.masks[""],
            masks_as_array(organ_ordering=names),
            lambda arr: np.moveaxis(arr, -1, 0),  # to allow map()
            curried.map(shift_center(points=centroid)),
            curried.map(
                crop_nd(
                    new_shape=(
                        config["input_height"],
                        config["input_width"],
                        config["input_depth"],
                        1,
                    ),
                    pad=True,
                )
            ),
            list,
            lambda masks: np.stack(masks, axis=-1),
            lambda mask: mask.astype(np.float32),
        )

    return tz.juxt(preprocess_volume, preprocess_mask)(scan)


@logger_wraps(level="INFO")
@curry
def preprocess_dataset(
    dataset: Iterable[PatientScan], config: PreprocessDataDict = model_config()
) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """
    Preprocess a dataset of PatientScan objects into (volume, masks) pairs

    Mask for multiple organs are stacked along the last dimension to have
    shape (height, width, depth, n_organs). An instance is filtered out if
    not all organs are present.
    """
    return tz.pipe(
        dataset,
        curried.map(preprocess_data(config=config)),
        curried.filter(lambda x: x[1] is not None),
    )  # type: ignore


@logger_wraps(level="INFO")
def construct_augmentor():
    """
    Preset augmentor to apply rotation, elastic transformation, flips, noise and gamma adjustments
    """
    return Compose(
        [
            Rotate((-10, 10), (-10, 10), (-10, 10), p=0.5),
            # ElasticTransform((0, 0.25), interpolation=2, p=0.1),
            Flip(0, p=0.2),
            Flip(1, p=0.2),
            Flip(2, p=0.2),
            GaussianNoise(var_limit=(0, 5), p=0.2),
            RandomGamma(gamma_limit=(80, 120), p=0.2),
        ],
        p=1.0,
    )


@logger_wraps(level="INFO")
@curry
def augment_data(
    image: np.ndarray, masks: np.ndarray, augmentor: Compose
) -> tuple[np.ndarray, np.ndarray]:
    """
    Augment data using the provided augmentor
    """
    return tz.pipe(
        # Pack in dictionary to work with augmentor
        {
            "image": image,
            "mask": masks,
        },
        lambda x: augmentor(**x),
        lambda x: (x["image"], x["mask"]),
    )  # type: ignore


@logger_wraps(level="INFO")
@curry
def augment_dataset(
    dataset: Iterable[tuple[np.ndarray, np.ndarray]], augmentor: Compose
) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """
    Augment data using the provided augmentor
    """
    return map(augment_data(augmentor=augmentor), dataset)