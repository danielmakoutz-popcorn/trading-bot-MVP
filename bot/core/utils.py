import yaml
from zoneinfo import ZoneInfo
from datetime import datetime

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg

def now_local(tz_name: str):
    return datetime.now(ZoneInfo(tz_name))
