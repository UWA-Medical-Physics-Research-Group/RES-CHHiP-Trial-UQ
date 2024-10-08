"""
Functions for logging
"""

import inspect
import logging
import warnings
from functools import wraps

from loguru import logger

from uncertainty.config import configuration


class __InterceptHandler(logging.Handler):
    """
    Intercept standard logging messages and redirect them to Loguru logger
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def config_logger(
    sink=configuration()["log_sink"],
    format=configuration()["log_format"],
    level=configuration()["log_level"],
    retention=configuration()["log_retention"],
):
    """
    Configure loguru logger settings and set it as as the default logger
    """
    logger.remove()
    logger.add(
        sink,
        format=format,
        backtrace=True,
        diagnose=True,
        level=level,
        retention=retention,
    )

    logging.basicConfig(handlers=[__InterceptHandler()], level=0, force=True)
    warnings.showwarning = lambda msg, *args, **kwargs: logger.warning(msg)


def logger_wraps(*, entry=True, exit=True, level="DEBUG"):
    """
    Logs entry and exit of a function
    """

    def wrapper(func):
        name = func.__name__

        @wraps(func)
        def wrapped(*args, **kwargs):
            result = None
            logger_ = logger.opt(depth=1)
            if entry:
                logger.log(
                    level,
                    f"Entering '{name}' (args={args}, kwargs={kwargs})",
                )
                result = func(*args, **kwargs)
            if exit:
                logger.log(level, f"Exiting '{name}' (result={result})")
            return result

        return wrapped

    return wrapper
