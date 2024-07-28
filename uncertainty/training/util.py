"""
Utility functions for model training, from model construction, data loading, and training loop
"""

from operator import methodcaller
from tests.context import PatientScan
from uncertainty.data.mask import get_organ_names, masks_as_array
from uncertainty.data.preprocessing import filter_roi, find_organ_roi, map_interval
from ..utils.logging import logger_wraps
from ..utils.wrappers import curry
from ..common.constants import model_config, HU_RANGE, ORGAN_MATCHES

from typing import Callable, Iterable, Optional

import numpy as np
from tensorflow.python.keras import Model
import tensorflow.keras as keras
import toolz as tz
import toolz.curried as curried
from scipy.ndimage import zoom
from volumentations import Compose, Rotate, ElasticTransform, Flip, GaussianNoise, RandomGamma


@logger_wraps(level="INFO")
@curry
def construct_model(
    model_constructor: Callable[[dict], Model],
    batches_per_epoch: int,
    config: dict = model_config(),
    show_model_info: bool = True,
):
    """
    Initialise model, set learning schedule and loss function and plot model
    """
    model = model_constructor(config)

    # Model outputs are logits, loss must apply softmax before computing loss
    loss = config["loss"](from_logits=config["final_layer_activation"] is None)

    # Number of iterations (batches) to pass before decreasing learning rate
    boundaries = [
        int(config["n_epochs"] * percentage * batches_per_epoch)
        for percentage in config["lr_schedule_percentages"]
    ]
    lr_schedule = config["lr_scheduler"](boundaries, config["lr_schedule_values"])

    model.compile(
        loss=loss,
        optimizer=keras.optimizers.Adam(learning_rate=lr_schedule),
        metrics=config["metrics"],
    )

    if show_model_info:
        keras.utils.plot_model(model, to_file="unet.png")
        model.summary()

    return model


@logger_wraps(level="INFO")
@curry
def preprocess_data(
    scan: PatientScan, config: dict = model_config()
) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """
    Preprocess a PatientScan object into (volume, masks) pairs

    Mask for multiple organs are stacked along the last dimension to have
    shape (height, width, depth, n_organs). Mask is `None` if not all organs are present.
    """
    def get_zoom_factors(old_shape: tuple[int, int, int]) -> tuple[float, float, float]:
        return tuple(
            to / from_
            for to, from_ in zip(
                (config["input_height"], config["input_width"], config["input_depth"], config["input_dim"]),
                old_shape,
            )
        )

    def preprocess_volume(scan: PatientScan) -> np.ndarray:
        return tz.pipe(
            scan.volume,
            #lambda v: zoom(
            #    v,
            #    get_zoom_factors(v.shape),
            #),
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
            names,
            masks_as_array(scan.masks[""]),
            #lambda m: zoom(
            #    m,
            #    get_zoom_factors(m.shape),
            #    order=1,
            #),
            lambda mask: mask.astype(np.float32),
        )

    return tz.juxt(preprocess_volume, preprocess_mask)(scan)

@logger_wraps(level="INFO")
@curry
def preprocess_dataset(
    dataset: Iterable[PatientScan], config: dict = model_config()
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
    )


@logger_wraps(level="INFO")
def construct_augmentor():
    """
    Preset augmentor to apply rotation, elastic transformation, flips, noise and gamma adjustments
    """
    return Compose([
        Rotate((-10, 10), (-10, 10), (-10, 10), p=0.5),
        ElasticTransform((0, 0.25), interpolation=2, p=0.1),
        Flip(0, p=0.2),
        Flip(1, p=0.2),
        Flip(2, p=0.2),
        GaussianNoise(var_limit=(0, 5), p=0.2),
        RandomGamma(gamma_limit=(80, 120), p=0.2),
    ], p=1.0)

@logger_wraps(level="INFO")
@curry
def augment_data(
    dataset: Iterable[tuple[np.ndarray, np.ndarray]], augmentor: Compose
) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """
    Augment data using the provided augmentor
    """
    return tz.pipe(
        dataset,
        # Pack in dictionary to work with augmentor
        curried.map(
            lambda x: {
                "image": x[0],
                "mask": x[1],
            }
        ),
        curried.map(lambda x: augmentor(**x)),
        curried.map(lambda x: (x["image"], x["mask"])),
    )

@logger_wraps(level="INFO")
@curry
def augment_dataset(
    dataset: Iterable[tuple[np.ndarray, np.ndarray]],
    augmentor: Compose
) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """
    Augment data using the provided augmentor
    """
    return map(augment_data(augmentor=augmentor), dataset)