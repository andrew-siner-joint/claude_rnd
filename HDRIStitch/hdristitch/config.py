"""Settings: built-in defaults (default_config.toml) overlaid with the user's
~/HDRIStitch/config.toml and then command-line overrides."""
import copy
import os
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib

DEFAULTS_FILE = Path(__file__).with_name("default_config.toml")


def home():
    """The user's HDRIStitch folder (config, rig templates)."""
    return Path(os.environ.get("HDRISTITCH_HOME", Path.home() / "HDRIStitch")).expanduser()


def templates_dir():
    return home() / "templates"


def defaults():
    with open(DEFAULTS_FILE, "rb") as f:
        return tomllib.load(f)


def _merge(base, extra, where=""):
    for key, value in extra.items():
        if key not in base:
            raise ValueError("Unknown setting '%s%s' in config" % (where, key))
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ValueError("Setting '%s%s' must be a table" % (where, key))
            _merge(base[key], value, where + key + ".")
        else:
            base[key] = value
    return base


def load(path=None):
    """Defaults + the user config (explicit path, else ~/HDRIStitch/config.toml
    if it exists)."""
    cfg = defaults()
    if path is None:
        candidate = home() / "config.toml"
        path = candidate if candidate.exists() else None
    elif not Path(path).exists():
        raise FileNotFoundError("Config file not found: %s" % path)
    if path is not None:
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
        cfg["_source"] = str(path)
    else:
        cfg["_source"] = "built-in defaults"
    return cfg


def override(cfg, dotted, value):
    """Set e.g. override(cfg, "color.gamut", "acescg")."""
    cfg = cfg if cfg is not None else defaults()
    section, key = dotted.split(".")
    if key not in cfg[section]:
        raise KeyError(dotted)
    cfg[section][key] = value
    return cfg


def copy_of(cfg):
    return copy.deepcopy(cfg)
