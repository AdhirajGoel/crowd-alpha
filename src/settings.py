"""Validate local configuration before requests or scheduled work."""
import json
import math
import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object")
    if config.get("engine") not in ("codex", "claude"):
        raise ValueError("engine must be codex or claude")
    for key in ("model", "executable"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    ZoneInfo(config["timezone"])
    date.fromisoformat(config["installed_date"])
    for key in ("x_monthly_usd", "x_resource_usd"):
        v=config.get(key)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{key} must be a positive finite number")
    hour=config.get("hour",17)
    if type(hour) is not int or not 0<=hour<=23:
        raise ValueError("hour must be an integer from 0 through 23")
    accounts=config.get("accounts")
    if not isinstance(accounts,dict) or not accounts:
        raise ValueError("Resolve at least one X handle with --resolve-accounts before running")
    if len(accounts)>20:
        raise ValueError("Use at most 20 accounts per installation")
    if any(not re.fullmatch(r"[0-9]+",str(i)) or not re.fullmatch(r"[A-Za-z0-9_]{1,15}",h) for i,h in accounts.items()):
        raise ValueError("Accounts must map numeric X IDs to valid handles")
    if len({h.lower() for h in accounts.values()})!=len(accounts):
        raise ValueError("Duplicate account handles")
    return config


def load(path):
    if not Path(path).is_file():
        raise ValueError("Run --setup first; see docs/setup.md")
    return validate(json.loads(Path(path).read_text()))
