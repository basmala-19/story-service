"""Text helpers shared by every stage (spec Section 5.0, ``norm_ar``)."""
from __future__ import annotations

import re
import unicodedata

_DIACRITICS = re.compile("[\u064B-\u0652\u0670]")
_TATWEEL = "\u0640"
_LETTER_MAP = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"}
)
# Arabic-Indic (U+0660..) and Extended Arabic-Indic (U+06F0..) digits -> Western.
_DIGIT_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"
)
_WS = re.compile(r"\s+")


def norm_ar(text: str | None) -> str:
    """Canonical form used by every quote / substring / similarity check.

    Removes diacritics and tatweel, unifies alef / yaa / taa-marbuta forms,
    maps Arabic-Indic digits to Western, lowercases Latin, turns punctuation
    and symbols into spaces and collapses whitespace.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = _DIACRITICS.sub("", t).replace(_TATWEEL, "")
    t = t.translate(_LETTER_MAP).translate(_DIGIT_MAP).lower()
    t = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in t)
    return _WS.sub(" ", t).strip()


def contains_phrase(haystack_norm: str, needle_norm: str) -> bool:
    """True when ``needle_norm`` occurs in ``haystack_norm`` on token boundaries.

    Both arguments must already be ``norm_ar`` output.
    """
    if not needle_norm:
        return False
    return f" {needle_norm} " in f" {haystack_norm} "


def word_count(text: str | None) -> int:
    """Whitespace word count on the original (un-normalised) text."""
    return len((text or "").split())
