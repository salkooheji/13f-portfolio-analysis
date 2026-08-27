"""Loads and validates config.yaml.

Fail loudly at startup if the config is malformed. A pipeline that
runs for ten minutes and then crashes on a missing key wastes time;
one that crashes in the first second with a clear message does not.
"""

import yaml

REQUIRED_KEYS = ("sec_user_agent", "managers", "quarters", "paths")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(f"config.yaml is missing keys: {missing}")

    for manager in config["managers"]:
        if "cik" not in manager or "name" not in manager:
            raise ValueError(f"Every manager needs cik and name, got: {manager}")

    return config
