# logging_setup.py
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def build_logger(name: str, filename: str, level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if not any(isinstance(h, RotatingFileHandler) and getattr(h, "_filename", None) == filename
               for h in logger.handlers):
        fh = RotatingFileHandler(
            LOG_DIR / filename, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh._filename = filename  # sentinel so we don’t add dup handlers
        fmt = logging.Formatter(
            fmt="%(asctime)sZ | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    return logger

app_logger   = build_logger("app",   "app.log")
trade_logger = build_logger("trades","trades.log")
