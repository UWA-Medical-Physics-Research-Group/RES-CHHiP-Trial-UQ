"""
Lightning modules for training models.
"""

import os
import time
from typing import Optional

import dill
import lightning as lit
import torch
from pytorch_lightning import seed_everything
from torch import nn
from torchmetrics.aggregation import RunningMean
from torchmetrics.classification import BinaryF1Score, MultilabelF1Score

from .loss import ConfidNetMSELoss, DeepSupervisionLoss, DiceBCELoss


class LitSegmentation(lit.LightningModule):
    """
    Wrapper class for PyTorch model to be used with PyTorch Lightning

    If deep supervision is enabled, then for a U-Net with n levels, a loss is calculated
    for each level and summed as
        L = w1 * L1 + w2 * L2 + ... + wn * Ln
    Where the weights halve for each level and are normalised to sum to 1.
    Output from the two levels in the lowest resolution are not used.
    SEE https://arxiv.org/abs/1809.10486

    Parameters
    ----------
    model : nn.Module
        PyTorch model to be trained. If training an ensemble, the ensemble
        members are created using the same model class and the same constructor
        arguments.
    config : dict
        Configuration dictionary
    class_weights : Optional[list[float]]
        Weights for each class in the loss function. Default is None.
        Weights are typically calculated using the number of pixels as
            n_background / n_foreground
    """

    def __init__(
        self,
        model: nn.Module,
        config: dict,
        class_weights: Optional[torch.Tensor] = None,
        save_hyperparams: bool = True,
    ):
        super().__init__()
        if save_hyperparams:
            self.save_hyperparameters(ignore=["model"])
            os.makedirs(config["model_checkpoint_path"], exist_ok=True)
            with open(
                os.path.join(config["model_checkpoint_path"], "config.pkl"), "wb"
            ) as f:
                dill.dump(config, f)

        self.config = config
        self.class_weights = class_weights
        # Original dice, used for evaluation
        self.dice_eval = MultilabelF1Score(
            num_labels=config["n_kernels_last"], zero_division=1
        )
        self.dice_eval_single = BinaryF1Score(zero_division=1)
        self.running_loss = RunningMean(window=10)
        self.running_dice = RunningMean(window=10)
        self.val_counter = 0
        self.val_batch_num = 0

        self.model = model
        self.deep_supervision = (
            hasattr(model, "deep_supervision") and model.deep_supervision
        )
        if self.deep_supervision:
            self.loss = DeepSupervisionLoss(DiceBCELoss(class_weights))
        else:
            self.loss = DiceBCELoss(class_weights)

    def forward(self, x, logits: bool = False):
        return self.model(x, logits)

    def training_step(self, batch: torch.Tensor):
        x, y = batch
        y_pred = self.model(x, logits=True)
        loss = self.loss(y_pred, y, logits=True)

        if self.deep_supervision:
            y_pred = y_pred[-1]
        dice = self.dice_eval(y_pred, y)

        self.running_dice(dice.detach())
        self.running_loss(loss.detach())
        self.log(
            "train_dice",
            self.running_dice.compute(),
            sync_dist=True,
            prog_bar=True,
        )
        self.log(
            "train_loss",
            self.running_loss.compute(),
            sync_dist=True,
            prog_bar=True,
        )
        return loss

    @torch.no_grad()
    def validation_step(self, batch: torch.Tensor):
        x, y = batch

        y_pred = self.model(x, logits=True)
        loss = self.loss(y_pred, y, logits=True)
        if self.deep_supervision:
            y_pred = y_pred[-1]
        dice = self.dice_eval(y_pred, y)

        # Get dice of each organ
        for channel, organ_name in zip(range(y_pred.shape[1]), ORGAN_MATCHES.keys()):
            with torch.no_grad():
                organ_dice = self.dice_eval_single(
                    y_pred[:, channel, ...],
                    y[:, channel, ...],
                )
                self.log(
                    f"val_dice_{organ_name}",
                    organ_dice,
                    sync_dist=True,
                    prog_bar=False,
                )

        if self.val_counter == 4:
            _dump_tensors(
                self.config["model_checkpoint_path"],
                x,
                y,
                y_pred,
                dice,
                loss,
                self.val_batch_num,
            )
            self.val_batch_num += 1
            self.val_counter = 0

        self.log(
            "val_loss",
            loss,
            sync_dist=True,
            prog_bar=True,
        )
        self.log(
            "val_dice",
            dice,
            sync_dist=True,
            prog_bar=True,
        )
        return loss

    def on_validation_epoch_end(self):
        self.val_counter += 1

    @torch.no_grad()
    def test_step(self, batch: torch.Tensor):
        x, y = batch
        y_pred = self.model(x, logits=False)
        loss = self.loss(y_pred, y, logits=False)
        if self.deep_supervision:
            y_pred = y_pred[-1]
        dice = self.dice_eval(y_pred, y)

        self.log("test_loss", loss, sync_dist=True, prog_bar=True)
        self.log("test_dice", dice, sync_dist=True, prog_bar=True)
        return loss

    def configure_optimizers(self):  # type: ignore
        optimiser = self.config["optimiser"](
            self.model.parameters(), **self.config["optimiser_kwargs"]
        )
        lr_scheduler = self.config["lr_scheduler"](optimiser)  # type: ignore
        return {"optimizer": optimiser, "lr_scheduler": lr_scheduler}


class LitConfidNet(lit.LightningModule):
    """ """

    def __init__(
        self,
        model: nn.Module,
        config: dict,
        class_weights: Optional[torch.Tensor] = None,
        save_hyperparams: bool = True,
    ):
        super().__init__()
        if save_hyperparams:
            self.save_hyperparameters(ignore=["model"])
            os.makedirs(config["model_checkpoint_path"], exist_ok=True)
            with open(
                os.path.join(config["model_checkpoint_path"], "config.pkl"), "wb"
            ) as f:
                dill.dump(config, f)

        self.config = config
        self.class_weights = class_weights
        self.running_loss = RunningMean(window=10)
        self.val_counter = 0
        self.val_batch_num = 0

        self.model = model
        self.loss = ConfidNetMSELoss()

    def forward(self, x, logits: bool = False):
        return self.model(x, logits)

    def training_step(self, batch: torch.Tensor):
        x, y = batch
        y_pred = self.model(x, logits=True)
        loss = self.loss(y_pred, y, logits=True)

        self.running_loss(loss.detach())
        self.log(
            "train_loss",
            self.running_loss.compute(),
            sync_dist=True,
            prog_bar=True,
        )
        return loss

    @torch.no_grad()
    def validation_step(self, batch: torch.Tensor):
        x, y = batch

        y_pred = self.model(x, logits=True)
        loss = self.loss(y_pred, y, logits=True)

        if self.val_counter == 4:
            _dump_tensors(
                self.config["model_checkpoint_path"],
                x,
                y,
                y_pred,
                0,
                loss,
                self.val_batch_num,
            )
            self.val_batch_num += 1
            self.val_counter = 0

        self.log(
            "val_loss",
            loss,
            sync_dist=True,
            prog_bar=True,
        )
        return loss

    def on_validation_epoch_end(self):
        self.val_counter += 1

    @torch.no_grad()
    def test_step(self, batch: torch.Tensor):
        x, y = batch
        y_pred = self.model(x, logits=False)
        loss = self.loss(y_pred, y, logits=False)
        if self.deep_supervision:
            y_pred = y_pred[-1]
        dice = self.dice_eval(y_pred, y)

        self.log("test_loss", loss, sync_dist=True, prog_bar=True)
        self.log("test_dice", dice, sync_dist=True, prog_bar=True)
        return loss

    def configure_optimizers(self):  # type: ignore
        optimiser = torch.optim.SGD(
            self.model.parameters(), **self.config["optimiser_kwargs"]
        )
        lr_scheduler = self.config["lr_scheduler"](optimiser)  # type: ignore
        return {"optimizer": optimiser, "lr_scheduler": lr_scheduler}
