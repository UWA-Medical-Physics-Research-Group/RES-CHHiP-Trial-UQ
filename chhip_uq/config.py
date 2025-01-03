"""
Reads configuration from a yaml file and returns a dictionary of configuration settings

A configuration object is a dictionary where each key has the format <prefix>__<parameter>
(to distinguish configuration arguments from normal keyword arguments).
"""

import inspect
import re
import sys
from functools import cache, reduce, wraps
from pathlib import Path
from typing import Callable

import toolz as tz
import yaml
from toolz import curried
from torch import nn, optim

_with_prefix = lambda prefix, dict_: tz.keymap(lambda k: f"{prefix}__{k}", dict_)


def _strs_to_torch_modules(config: dict) -> dict:
    """
    Convert strings to torch modules
    """

    def apply_if_exists_pipe(value, transformations: list[tuple[str, Callable]]):
        """
        Pipe value through list of transformations, each only applied if the key exists
        """
        return tz.pipe(
            value,
            *[
                transformation
                for key, transformation in transformations
                if key in config
            ],
        )

    transforms = [
        (
            "activation",
            lambda _dict: tz.assoc_in(
                _dict,
                keys=["activation"],
                value=getattr(nn, _dict["activation"]),
            ),
        ),
        (
            "final_layer_activation",
            lambda _dict: tz.assoc_in(
                _dict,
                keys=["final_layer_activation"],
                value=getattr(nn, _dict["final_layer_activation"]),
            ),
        ),
        (
            "initialiser",
            lambda _dict: tz.assoc_in(
                _dict,
                keys=["initialiser"],
                value=getattr(nn.init, _dict["initialiser"]),
            ),
        ),
        (
            "optimiser",
            lambda _dict: tz.assoc_in(
                _dict,
                keys=["optimiser"],
                value=getattr(optim, _dict["optimiser"]),
            ),
        ),
        (
            "lr_scheduler",
            lambda _dict: tz.assoc_in(
                _dict,
                keys=["lr_scheduler"],
                value=getattr(optim.lr_scheduler, _dict["lr_scheduler"]),
            ),
        ),
    ]

    return apply_if_exists_pipe(config, transforms)  # type: ignore


@cache
def data_config(config_path: str | Path = "configuration.yaml") -> dict:
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    return _with_prefix("data", config["data"]) if "data" in config else {}


@cache
def unet_config(config_path: str | Path = "configuration.yaml") -> dict:
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    config["unet"] = _strs_to_torch_modules(config["unet"])

    return _with_prefix("unet", config["unet"]) if "unet" in config else {}


def confidnet_config(config_path: str | Path = "configuration.yaml") -> dict:
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    config["confidnet"] = _strs_to_torch_modules(config["confidnet"])
    return (
        _with_prefix("confidnet", config["confidnet"]) if "confidnet" in config else {}
    )


@cache
def logger_config(config_path: str | Path = "configuration.yaml") -> dict:
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    # Replace string with corresponding file object if present
    mapping = {"stdout": sys.stdout, "stderr": sys.stderr}
    config["logger"]["sink"] = mapping.get(
        config["logger"]["sink"], config["logger"]["sink"]
    )

    return _with_prefix("logger", config["logger"]) if "logger" in config else {}


@cache
def training_config(config_path: str | Path = "configuration.yaml") -> dict:
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    return _with_prefix("training", config["training"]) if "training" in config else {}


def configuration(config_path: str | Path = "configuration.yaml") -> dict:
    """
    The entire configuration for the project
    """
    return reduce(
        tz.merge,
        [
            data_config(config_path),
            unet_config(config_path),
            logger_config(config_path),
            training_config(config_path),
            confidnet_config(config_path),
        ],
    )


def auto_match_config(*, prefixes: list[str]):
    """
    Automatically pass configuration values to function parameters

    A configuration object is a dictionary where each key has the format
    `<prefix>__<parameter>.` Prefixes are stripped before being passed to
    the function parameters. Subset of the dictionary can be selected
    by specifying the prefixes of the keys to select.

    Note that if a function have the parameter `kwargs`, the entire
    configuration dictionary with the prefixes kept will be passed to it.
    This is useful for passing the configuration dictionary to inner functions.

    the remaining
    configuration dictionary after passing the values to the other
    parameters will be passed to the `kwargs` parameter. This is useful
    for passing the configuration dictionary to inner functions.

    If a function is called with explicit keyword arguments with the same
    name in the configuration dictionary, the explicit keyword argument
    will override the value in the configuration dictionary. E.g. `a=10` in
    `my_func(a=10, **config)` will take precedence over `config["a"]`. Note
    that you CANNOT pass value by positional argument for parameter with name
    `k` if `k` is a key in the configuration dictionary - i.e. overwriting
    configuration values must be explicit by passing them as keyword arguments.

    Parameters
    ----------
    prefixes : str
        Beginning string of the keys delimited by "__" in the configuration
        dictionary. If specified, only the keys that have the prefix
        are passed to the function. If two keys have the same prefix,
        the value in the dictionary that appears later in the configuration
        dictionary is used. Mandatory as this is the only way to distinguish
        between configuration values and other keyword arguments.

    Examples
    --------
    >>> @auto_match_config(prefixes=["1"])
    ... def test2(c, d):
    ...    print(c, d)
    >>> @auto_match_config(prefixes=["1", "2"])
    ... def test(a, b, **kwargs):
    ...    test2(**kwargs)
    ...    print(a + b)
    >>> config = {"1__c": 30, "1__d": 4, "2__a": 10, "2__b": 2000}
    >>> test(b=5, **config)  # b=5 overrides config["2__b"]
    30 4
    15
    >>> @auto_match_config(prefixes=["a"])
    ... def test2(c, common_param):
    ...    print(c, common_param)
    >>> @auto_match_config(prefixes=["a"])
    ... def test(a, common_param, **kwargs):
    ...     print(a, common_param)
    ...     test2(**kwargs)  # no need to pass common_param in test2
    >>> config = {"a__a": 30, "a__common_param": 4, "a__c": 7}
    >>> test(**config)
    30 4
    7 4
    >>> test(4, **config) # ValueError: Multiple values for parameter 'a'
    """

    def wrapper(func):

        @wraps(func)
        def wrapped(*args, **kwargs):
            strip_prefix = lambda k: re.sub(r"^[^_]*__", "", k)

            params = inspect.signature(func).parameters
            # length 1 means key don't begin with a prefix, hence not from config
            non_config_kwargs = tz.keyfilter(lambda k: len(k.split("__")) == 1, kwargs)
            filtered_kwargs = tz.pipe(
                kwargs,
                # all keys with a prefix (i.e. from config)
                curried.keyfilter(lambda k: len(k.split("__")) > 1),
                (
                    curried.keyfilter(lambda k: k.split("__")[0] in prefixes)
                    if prefixes
                    else tz.identity
                ),
                # let non_config_kwargs override config values by adding non-config kwargs later
                lambda config_kwargs: tz.merge(config_kwargs, non_config_kwargs),
                curried.keymap(strip_prefix),
                (
                    curried.keyfilter(lambda k: k in params)
                    if "kwargs" not in params
                    else lambda config_kwargs: curried.merge(kwargs, config_kwargs)
                ),
            )

            return func(*args, **filtered_kwargs)

        return wrapped

    return wrapper
