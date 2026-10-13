"""The Havas logo in the reports.

Two variants, chosen by what each report puts behind it:
  white  the HTML/PDF header, a solid black band with white text;
  red    the Word file, a white page whose site name is already in Havas red.
The black variant exists too but no report has a use for it yet.

A missing or unreadable file costs the logo, never the report.
"""
from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
VARIANTS = {"white": "havas_logo_white.png", "red": "havas_logo_red.png"}


def logo_path(variant: str) -> Path | None:
    """The logo file for a variant, or None when it is not there."""
    name = VARIANTS.get(variant)
    path = ASSETS / name if name else None
    return path if path is not None and path.is_file() else None


@lru_cache(maxsize=4)
def logo_data_uri(variant: str) -> str:
    """The logo as a data: URI, so an HTML report stays one self-contained file
    (the PDF is rendered from it by a browser that would otherwise need the path)."""
    path = logo_path(variant)
    if path is None:
        log.warning("[branding] no %s logo at %s", variant, ASSETS)
        return ""
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as e:
        log.warning("[branding] could not read %s: %s", path, e)
        return ""
