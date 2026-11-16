"""
Logging setup for VibeFinder.

Every request writes its parsed profile, retrieved song IDs, validation
result, and any fallbacks to logs/vibefinder.log so runs can be audited.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "vibefinder.log"
ROOT_LOGGER = "vibefinder"


def setup_logging(level: str | None = None) -> logging.Logger:
    """Configure the 'vibefinder' logger once. Safe to call repeatedly (e.g. Streamlit reruns)."""
    logger = logging.getLogger(ROOT_LOGGER)
    if logger.handlers:
        return logger

    level_name = (level or os.getenv("VIBEFINDER_LOG_LEVEL", "INFO")).upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)

    # Only surface warnings and errors on the console so CLI output stays clean.
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(console)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Child logger, e.g. get_logger('agent') -> 'vibefinder.agent'."""
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")
