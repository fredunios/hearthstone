from __future__ import annotations

import configparser
from pathlib import Path

from .config import LOG_CONFIG_PATH

POWER_SETTINGS = {
    "LogLevel": "1",
    "FilePrinting": "true",
    "ConsolePrinting": "false",
    "ScreenPrinting": "false",
    "Verbose": "true",
}


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    if path.exists():
        parser.read(path, encoding="utf-8")
    return parser


def is_power_log_enabled(path: Path = LOG_CONFIG_PATH) -> bool:
    parser = _read(path)
    if not parser.has_section("Power"):
        return False
    section = parser["Power"]
    return all(section.get(k, "").lower() == v for k, v in POWER_SETTINGS.items())


def enable_power_log(path: Path = LOG_CONFIG_PATH) -> bool:
    """Make sure log.config enables verbose Power logging.

    Returns True if the file was changed, which means Hearthstone has to be
    restarted before Power.log appears.
    """
    if is_power_log_enabled(path):
        return False
    parser = _read(path)
    if not parser.has_section("Power"):
        parser.add_section("Power")
    for key, value in POWER_SETTINGS.items():
        parser["Power"][key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        parser.write(fh, space_around_delimiters=False)
    return True
