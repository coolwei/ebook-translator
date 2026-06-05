from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(output_dir: Path, level: str = "info") -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

    logger = logging.getLogger("ebook_translator")
    logger.setLevel(log_level)
    logger.handlers.clear()

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(log_level)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    run_handler = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    run_handler.setLevel(log_level)
    run_handler.setFormatter(fmt)
    logger.addHandler(run_handler)

    error_handler = logging.FileHandler(log_dir / "errors.log", encoding="utf-8")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(fmt)
    logger.addHandler(error_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("ebook_translator")
