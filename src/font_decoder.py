"""Decode fussball.de's font obfuscation.

Scores and date labels are rendered as private-use codepoints that only a
per-response webfont maps back to real characters. The font's cmap gives
codepoint -> glyph name, and the glyph name is the plain character.
"""

import logging
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional

import requests
from fontTools import ttLib

from config import CACHE_DIR, FUSSBALL_DE_BASE_URL, REQUEST_TIMEOUT_SECONDS, USER_AGENT

logger = logging.getLogger(__name__)

# Glyph names are standard PostScript names. Score fonts carry only digits and
# "hyphen" (the placeholder for a match without a result); date fonts also carry
# the letters of the German weekday abbreviations and punctuation.
GLYPH_TO_CHAR = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "hyphen": "-",
    "period": ".",
    "comma": ",",
    "colon": ":",
    "space": " ",
}
GLYPH_TO_CHAR.update({c: c for c in "abcdefghijklmnopqrstuvwxyz"})
GLYPH_TO_CHAR.update({c: c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})

PRIVATE_USE_START = 0xE000

# Where downloaded fonts are kept. Tests point this at their own fixture fonts
# so they run offline without depending on the scrape cache.
font_cache_dir = Path(CACHE_DIR) / "fonts"

_memory_cache: Dict[str, Dict[int, str]] = {}


def get_font_mapping(font_name: str) -> Dict[int, str]:
    """codepoint -> character, for one obfuscation font. Cached on disk so a
    cached page can be re-parsed offline."""
    if font_name in _memory_cache:
        return _memory_cache[font_name]

    font_cache_dir.mkdir(parents=True, exist_ok=True)
    font_file = font_cache_dir / f"{font_name}.woff"

    if font_file.exists():
        data = font_file.read_bytes()
    else:
        url = f"{FUSSBALL_DE_BASE_URL}/export.fontface/-/format/woff/id/{font_name}/type/font"
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.content
        font_file.write_bytes(data)

    cmap = ttLib.TTFont(BytesIO(data)).getBestCmap()
    mapping = {
        codepoint: GLYPH_TO_CHAR[glyph]
        for codepoint, glyph in cmap.items()
        if glyph in GLYPH_TO_CHAR
    }
    if not mapping:
        raise ValueError(f"Font {font_name} yielded no usable glyphs")

    _memory_cache[font_name] = mapping
    return mapping


def decode(text: str, font_name: str) -> Optional[str]:
    """Decode obfuscated text. Characters outside the private use area (spaces,
    separators) pass through. Returns None if a private-use char is unmapped."""
    mapping = get_font_mapping(font_name)
    out = []
    for char in text:
        codepoint = ord(char)
        if codepoint < PRIVATE_USE_START:
            out.append(char)
            continue
        decoded = mapping.get(codepoint)
        if decoded is None:
            logger.debug(f"Unmapped codepoint U+{codepoint:04X} in font {font_name}")
            return None
        out.append(decoded)
    return "".join(out)
