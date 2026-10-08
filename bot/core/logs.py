import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    log_dir = Path(log_cfg.get("dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_cfg.get("file", "bot.log")
    max_bytes = int(log_cfg.get("max_bytes", 1_048_576))
    backup_count = int(log_cfg.get("backup_count", 5))

    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger
