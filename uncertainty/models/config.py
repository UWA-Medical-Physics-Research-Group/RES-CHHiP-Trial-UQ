from typing import Any, Final
import keras
from toolz import memoize
from typing import TypedDict

ModelConfig = TypedDict(
    "ModelConfig",
    {
        "data_dir": str,
        "input_height": int,
        "input_width": int,
        "input_depth": int,
        "input_channel": int,
        "kernel_size": tuple[int, int, int],
        "n_convolutions_per_block": int,
        "use_batch_norm": bool,
        "activation": str,
        "n_kernels_init": int,
        "n_levels": int,
        "n_kernels_last": int,
        "final_layer_activation": str,
        "model_checkpoint_path": str,
        "n_epochs": int,
        "batch_size": int,
        "metrics": list[str],
        "initializer": str,
        "optimizer": Any,
        "loss": Any,
        "lr_scheduler": Any,
        "lr_schedule_percentages": list[float],
        "lr_schedule_values": list[float],
        "n_kernels_per_block": list[int],
    },
)


@memoize
def model_config() -> ModelConfig:
    """
    Preset configuration for U-Net model
    """
    n_levels: Final[int] = 5  # used to calculate input shape
    config: ModelConfig = {
        # --------- Data settings ---------
        "data_dir": "",
        # Data are formatted as (height, width, depth, dimension)
        # Ensure each dimension is divisible by 2 ** (n_levels - 1) so no row/col is discarded
        # because it's divided by two (n_levels - 1) times in the U-Net
        "input_height": (2**n_levels) * 10,
        "input_width": (2**n_levels) * 13,
        "input_depth": (2**n_levels) * 6,
        "input_channel": 3,  # Number of organs, mask only
        # ------- ConvolutionBlock settings  --------
        "kernel_size": (3, 3, 3),
        "n_convolutions_per_block": 1,
        "use_batch_norm": False,
        "activation": "gelu",
        # ------- Encoder/Decoder settings -------
        # Number of kernels in first level of Encoder, doubles/halves at each level in Encoder/Decoder
        "n_kernels_init": 64,
        # Number of resolutions/blocks; height of U-Net
        "n_levels": n_levels,
        # Number of class to predict
        "n_kernels_last": 1,
        # Use sigmoid if using binary crossentropy, softmax if using categorical crossentropy
        # Note if using softmax, n_kernels_last should be equal to number of classes (e.g. 2 for binary segmentation)
        # If None, loss will use from_logits=True
        "final_layer_activation": "sigmoid",
        # ------- Overall U-Net settings -----------------
        "model_checkpoint_path": "./checkpoints/checkpoint.model.keras",
        "n_epochs": 1,
        "batch_size": 32,
        "metrics": ["accuracy"],
        "initializer": "he_normal",  # For kernel initialisation
        "optimizer": keras.optimizers.Adam,
        "loss": keras.losses.BinaryCrossentropy,
        # Learning rate scheduler, decrease learning rate at certain epochs
        "lr_scheduler": keras.optimizers.schedules.PiecewiseConstantDecay,
        # Percentage of training where learning rate is decreased
        "lr_schedule_percentages": [0.2, 0.5, 0.8],
        # Gradually decrease learning rate, starting at first value
        "lr_schedule_values": [3e-4, 1e-4, 1e-5, 1e-6],
        # ------------------ Derived settings --------------------
        "n_kernels_per_block": [],  # Calculated in the next line
    }
    # Explicitly calculate number of kernels per block (for Encoder)
    config["n_kernels_per_block"] = [
        config["n_kernels_init"] * (2**block_level)
        for block_level in range(config["n_levels"])
    ]

    return config