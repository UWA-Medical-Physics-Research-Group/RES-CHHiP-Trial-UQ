"""
Set of methods to load and save NIfTI files
"""

import itertools
from typing import Generator, List
import re

import toolz as tz
import toolz.curried as curried
import numpy as np
import nibabel as nib

from uncertainty.utils.logging import logger_wraps

from .mask import Mask
from .patient_scan import PatientScan
from ..utils.common import unpack_args
from ..utils.wrappers import curry
from ..utils.path import resolve_path_placeholders
from ..utils.string import placeholder_matches


def __assert_placeholders(path: str, placeholders: list[str] | str, path_name: str):
    if isinstance(placeholders, str):
        placeholders = [placeholders]
    assert all(
        pattern in path for pattern in placeholders
    ), f"{path_name} must contain " + ", ".join(placeholders)


@logger_wraps(level="INFO")
@curry
def load_patient_scan(
    volume_path_pattern: str, mask_path_pattern: str, patient_id: str
) -> PatientScan:
    __assert_placeholders(volume_path_pattern, "{patient_id}", "volume_path_pattern")
    __assert_placeholders(
        mask_path_pattern,
        ["{patient_id}", "{organ}", "{observer}"],
        "mask_path_pattern",
    )

    def non_required_placeholders(path: str) -> List[str]:
        """Return placeholders that are not the three required tags"""
        return [
            match_[1:-1]
            for match_ in re.findall(r"({.*?})", path)
            if match_ not in ("{patient_id}", "{organ}", "{observer}")
        ]

    return tz.pipe(
        (volume_path_pattern, mask_path_pattern),
        # Patient ID is already known, so replace it in both paths
        curried.map(
            lambda path_pattern: path_pattern.replace("{patient_id}", patient_id)
        ),
        curried.map(
            # Make unique, done here so set() don't mess up volume-mask path order
            # Let's hope set() don't return more than 1 path lmao
            lambda path: set(
                list(
                    resolve_path_placeholders(
                        path, placeholders=non_required_placeholders(path)
                    )
                )
            )
        ),
        tz.concat,
        tuple,  # Hopefully [vol_path, mask_path] if set() returned 1 path
        unpack_args(
            lambda vol_path, mask_path: PatientScan(
                patient_id,
                load_volume(vol_path),
                list(load_mask_multiple_observers(mask_path)),
            )
        ),
    )


@logger_wraps(level="INFO")
def load_patient_scans(
    volume_path_pattern: str, mask_path_pattern: str
) -> Generator[PatientScan, None, None]:
    """
    Load PatientScan list in directory of NIfTI files

    @param volume_path_pattern: pattern to match NIfTI files for the volume,
        must contain placeholder {patient_id}, e.g. "/path/to/{patient_id}_CT.nii.gz"
    @param mask_path_pattern: pattern to match NIfTI files for the masks, must
        contain placeholders {patient_id}, {organ}, and {observer}, e.g.
        "/path/to/{patient_id}_CT_{organ}_{observer}.nii.gz"
    """
    __assert_placeholders(volume_path_pattern, "{patient_id}", "volume_path_pattern")
    __assert_placeholders(
        mask_path_pattern,
        ["{patient_id}", "{organ}", "{observer}"],
        "mask_path_pattern",
    )

    return tz.pipe(
        volume_path_pattern,
        resolve_path_placeholders(placeholders=["patient_id"]),
        placeholder_matches(pattern=volume_path_pattern, placeholders=["patient_id"]),
        curried.map(
            lambda patient_id: load_patient_scan(
                volume_path_pattern, mask_path_pattern, *patient_id
            )
        ),
    )


@logger_wraps(level="INFO")
def load_volume(nifti_path: str) -> np.ndarray:
    """
    Load 3D volume from NIfTI file, return generator of 2D slices (lazy loading)
    """
    return nib.load(nifti_path).get_fdata()


@logger_wraps(level="INFO")
@curry
def load_mask(mask_path_pattern: str, observer: str = "") -> Mask:
    """
    Extract Mask of different organs from a single observer for a single patient

    @param mask_path_pattern: pattern to match NIfTI files containing placeholder
        {organ}
    @param observer: observer name
    """
    __assert_placeholders(mask_path_pattern, "{organ}", "mask_path_pattern")

    return tz.pipe(
        mask_path_pattern,
        resolve_path_placeholders(placeholders=["organ"]),
        itertools.tee,
        # Zip paths with organ names
        unpack_args(
            lambda path_it1, path_it2: zip(
                path_it1,
                placeholder_matches(path_it2, mask_path_pattern, ["organ"]),
            )
        ),
        lambda path_organ_pairs: {
            # organ is a tuple so need unpacking, e.g. ("Bladder",)
            organ[0]: load_volume(path)
            for path, organ in path_organ_pairs
        },
        lambda organ_dict: Mask(organ_dict, observer),
    )


@logger_wraps(level="INFO")
def load_mask_multiple_observers(mask_path_pattern: str) -> Generator[Mask, None, None]:
    """
    Extract Mask of different organs from multiple observers for a single patient

    @param mask_path_pattern: pattern to match NIfTI files containing placeholder
        {observer} and {organ}
    """
    __assert_placeholders(
        mask_path_pattern, ["{organ}", "{observer}"], "mask_path_pattern"
    )
    return tz.pipe(
        mask_path_pattern,
        resolve_path_placeholders(placeholders=["observer"]),
        itertools.tee,
        # Zip paths with observer names
        unpack_args(
            lambda path_it1, path_it2: zip(
                path_it1,
                placeholder_matches(path_it2, mask_path_pattern, ["observer"]),
            )
        ),
        set,
        curried.map(unpack_args(lambda path, observer: load_mask(path, *observer))),
    )
