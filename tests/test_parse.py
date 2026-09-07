"""Snapshot tests: the parser survives real fussball.de markup.

The fixtures are unmodified saved responses. Their obfuscation fonts are in
data/raw/fonts/, so these tests run offline.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import font_decoder  # noqa: E402
from parse import parse_matchday, validate  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SEASON = "2025/26"

# Use the fonts committed next to the fixtures, not the scrape cache, so the
# tests run offline on a clean checkout.
font_decoder.font_cache_dir = FIXTURES / "fonts"


def load(matchday: int):
    html = (FIXTURES / f"staffel_2025-26_md{matchday:02d}.html").read_text(encoding="utf-8")
    return parse_matchday(html, SEASON, matchday)


def test_matchday_1_scores():
    matches = load(1)
    assert len(matches) == 7
    by_home = {m["home_team"]: m for m in matches}

    cochem = by_home["SpvGG Cochem"]
    assert (cochem["away_team"], cochem["home_goals"], cochem["away_goals"]) == (
        "TuS Rheinböllen",
        2,
        1,
    )
    assert cochem["date"] == "2025-08-08"
    assert cochem["status"] == "played"

    # A goalless draw must not be mistaken for a match without a result.
    blankenrath = by_home["SV Blankenrath"]
    assert (blankenrath["home_goals"], blankenrath["away_goals"]) == (0, 0)
    assert blankenrath["status"] == "played"


def test_matchday_3_excludes_postponed_matches():
    """The page also lists matches 'Verlegte Spiele ausserhalb des Spieltages',
    which belong to other matchdays and are parsed there."""
    matches = load(3)
    assert len(matches) == 7
    assert all(m["matchday"] == 3 for m in matches)

    by_home = {m["home_team"]: m for m in matches}
    assert (by_home["SpvGG Cochem"]["home_goals"], by_home["SpvGG Cochem"]["away_goals"]) == (5, 0)
    # SSV Boppard and TuS Rheinboellen appear only in the postponed block here.
    assert "SSV Boppard" not in by_home


def test_dates_inherit_within_a_date_group():
    """Only the first row of a date group carries the date label."""
    matches = load(3)
    assert [m["date"] for m in matches] == [
        "2025-08-22",
        "2025-08-23",
        "2025-08-23",
        "2025-08-24",
        "2025-08-24",
        "2025-08-24",
        "2025-08-24",
    ]


def test_match_ids_are_stable_and_unique():
    matches = load(1) + load(3)
    ids = [m["match_id"] for m in matches]
    assert len(set(ids)) == len(ids)
    assert all(len(i) == 32 and i.isalnum() for i in ids)


def test_validate_rejects_an_incomplete_season():
    with pytest.raises(ValueError, match="expected"):
        validate(load(1), SEASON)
