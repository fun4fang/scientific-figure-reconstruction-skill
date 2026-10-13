from __future__ import annotations

import re


# The frozen analysis output sometimes contains conservative, human-readable
# colour descriptions rather than CSS/OOXML literals.  Keep this table small,
# deterministic, and renderer-neutral so PPTX and SVG cannot interpret the
# same canonical style differently.
_DESCRIPTIVE_COLOURS = {
    "black": "000000",
    "white": "FFFFFF",
    "gray": "808080",
    "grey": "808080",
    "light gray": "D3D3D3",
    "light grey": "D3D3D3",
    "very light gray": "F2F2F2",
    "very light grey": "F2F2F2",
    "light blue": "D9EAF7",
    "pale blue": "D9EAF7",
    "pale blue gradient glow": "D9EAF7",
    "light red": "F4CCCC",
    "translucent red": "F4CCCC",
    "salmon red": "FA8072",
    "mint green": "B7E1CD",
    "pale yellow": "FFF2CC",
    "pale yellow gradient glow": "FFF2CC",
    "orange translucent": "F4B183",
    "cyan blue translucent": "4FC3F7",
    "dark gray and blue gradient like shading": "607D8B",
    "purple": "7030A0",
}


def color_hex(value: str | None, default: str) -> str:
    """Return a deterministic six-digit renderer-neutral colour.

    Unsupported compound descriptions fail to the caller's explicit default;
    they are never passed through to SVG where invalid CSS becomes black.
    """

    if not value:
        return default.upper().lstrip("#")
    stripped = value.strip().lstrip("#")
    normalized = stripped.upper()
    if len(normalized) == 6 and all(character in "0123456789ABCDEF" for character in normalized):
        return normalized
    key = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return _DESCRIPTIVE_COLOURS.get(key, default.upper().lstrip("#"))


def svg_color(value: str | None, default: str) -> str:
    return f"#{color_hex(value, default)}"
