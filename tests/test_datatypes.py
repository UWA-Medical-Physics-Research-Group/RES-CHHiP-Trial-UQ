import numpy as np
import pytest

from .context import Mask


class TestMask:

    # Retrieve mask for a valid organ
    def test_retrieve_mask_for_valid_organ(self):

        # Initialize a Mask object with organ-mask pairs
        mask = Mask(
            {
                "organ_1": np.array([[1, 1], [0, 1]]),
                "organ_2": np.array([[0, 1], [1, 0]]),
            }
        )

        # Retrieve the mask for "organ_1"
        organ_1_mask = mask["organ_1"]

        # Assert that the retrieved mask is correct
        np.testing.assert_array_equal(organ_1_mask, np.array([[1, 1], [0, 1]]))
