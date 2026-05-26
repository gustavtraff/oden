"""Extract geographic coordinates from Signal message text."""

from __future__ import annotations

import logging
import re

import mgrs

logger = logging.getLogger(__name__)

# Optional minus, digits, dot, digits (e.g. 59.514828 or -33.8688)
_COORD = r"(-?\d+\.\d+)"

# Map URL patterns — tried before MGRS (first match wins).
_LOCATION_URL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"https://(?:www\.)?(?:maps\.)?google\.com/maps\?q={_COORD}(?:%2[cC]|,){_COORD}"),
    re.compile(rf"https://maps\.apple\.com/\?(?:[^\s]*&)?(?:q|ll)={_COORD},{_COORD}"),
    re.compile(rf"https://(?:www\.)?openstreetmap\.org/?\?(?:[^\s]*&)?mlat={_COORD}&(?:[^\s]*&)?mlon={_COORD}"),
    re.compile(rf"https://(?:www\.)?openstreetmap\.org/?[^\s]*#map=[\d.]+/{_COORD}/{_COORD}"),
]

# Invisible / directional formatting characters sometimes inserted by Signal clients.
_INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")

# MGRS after "Ställe:" — zone, band, 100 km square, easting, northing (5 digits each).
_MGRS_STALLE_PATTERN = re.compile(
    r"(?i)St[aä]lle:\s*(\d{1,2})([C-HJ-NP-X])\s*([A-HJ-NP-Z]{2})\s*(\d{5})\s*(\d{5})",
)

_MGRS_CONVERTER = mgrs.MGRS()


def normalize_message_text(msg: str) -> str:
    """Strip invisible Unicode formatting characters from message text."""
    return _INVISIBLE_CHARS.sub("", msg)


def _format_coord(value: float) -> str:
    """Format a WGS84 coordinate as a decimal-degree string."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def extract_coordinates_from_url(msg: str) -> tuple[str, str] | None:
    """Extract latitude and longitude from a map URL in *msg*."""
    for pattern in _LOCATION_URL_PATTERNS:
        match = pattern.search(msg)
        if match:
            return match.group(1), match.group(2)
    return None


def extract_coordinates_from_mgrs_stalle(msg: str) -> tuple[str, str] | None:
    """Extract WGS84 coordinates from an MGRS reference on a ``Ställe:`` line."""
    normalized = normalize_message_text(msg)
    match = _MGRS_STALLE_PATTERN.search(normalized)
    if not match:
        return None

    zone, band, square, easting, northing = match.groups()
    mgrs_string = f"{zone}{band}{square}{easting}{northing}"

    try:
        lat, lon = _MGRS_CONVERTER.toLatLon(mgrs_string)
    except Exception as exc:
        logger.warning("Failed to convert MGRS '%s' to WGS84: %s", mgrs_string, exc)
        return None

    return _format_coord(lat), _format_coord(lon)


def extract_location(msg: str | None) -> tuple[str, str] | None:
    """Extract WGS84 coordinates from *msg* (map URL first, then MGRS ``Ställe:``)."""
    if not msg:
        return None

    coords = extract_coordinates_from_url(msg)
    if coords:
        return coords

    return extract_coordinates_from_mgrs_stalle(msg)


def extract_coordinates(msg: str) -> tuple[str, str] | None:
    """Backward-compatible alias for :func:`extract_location`."""
    return extract_location(msg)
