"""Turn cached matchday pages into matches.csv rows, with validation."""

import csv
import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from config import KNOWN_TEAMS, canonical_team
from font_decoder import decode
from scrape import fetch_season

logger = logging.getLogger(__name__)

FIELDNAMES = [
    "match_id",
    "season",
    "matchday",
    "date",
    "home_team",
    "away_team",
    "home_goals",
    "away_goals",
    "status",
]

# All selectors in one place, so a layout change is a one-line fix.
SEL = {
    "date_cell": "column-date",
    "score_cell": "column-score",
    "club_name": "club-name",
    "score_left": "score-left",
    "score_right": "score-right",
}

# Rows after this separator belong to other matchdays and are parsed there.
POSTPONED_MARKER = "Verlegte Spiele außerhalb des Spieltages"

RE_DATE_HREF = re.compile(r"/spieldatum/(\d{4}-\d{2}-\d{2})/")
RE_MATCH_ID = re.compile(r"/spiel/([A-Z0-9]+)(?:$|[/?#])")
RE_DATE_TEXT = re.compile(r"(\d{2})\.(\d{2})\.(\d{2,4})")


def _extract_date(date_cell) -> Optional[str]:
    """Kickoff date. Only the first row of a date group carries the date, so
    rows with neither a link nor a label inherit from the row above."""
    link = date_cell.find("a", href=RE_DATE_HREF)
    if link:
        return RE_DATE_HREF.search(link["href"]).group(1)

    for span in date_cell.find_all("span", attrs={"data-obfuscation": True}):
        decoded = decode(span.get_text(strip=True), span["data-obfuscation"])
        if not decoded:
            continue
        found = RE_DATE_TEXT.search(decoded)
        if found:
            day, month, year = found.groups()
            return f"{'20' + year if len(year) == 2 else year}-{month}-{day}"
    return None


def _extract_goals(span) -> Optional[int]:
    """Digit behind the font obfuscation, or None if the match has no result.
    An unplayed match renders the "hyphen" glyph as a placeholder."""
    if span is None:
        return None
    font = span.get("data-obfuscation")
    text = span.get_text(strip=True)
    if not font or not text:
        return None
    decoded = decode(text, font)
    return int(decoded) if decoded and decoded.isdigit() else None


def parse_matchday(html: str, season: str, matchday: int) -> List[Dict]:
    """Matches belonging to this matchday. Postponed matches listed underneath
    are skipped, since they appear again on the matchday they belong to."""
    soup = BeautifulSoup(html, "html.parser")
    matches: List[Dict] = []
    last_date = None

    for row in soup.find_all("tr"):
        if POSTPONED_MARKER in row.get_text():
            break

        score_cell = row.find("td", class_=SEL["score_cell"])
        date_cell = row.find("td", class_=SEL["date_cell"])
        if not score_cell or not date_cell:
            continue

        clubs = row.find_all(class_=SEL["club_name"])
        score_link = score_cell.find("a", href=RE_MATCH_ID)
        if len(clubs) != 2 or not score_link:
            logger.warning(f"{season} md{matchday}: unparseable row, skipping")
            continue

        match_date = _extract_date(date_cell) or last_date
        if not match_date:
            logger.warning(f"{season} md{matchday}: row without a date, skipping")
            continue
        last_date = match_date

        home_goals = _extract_goals(score_cell.find("span", class_=SEL["score_left"]))
        away_goals = _extract_goals(score_cell.find("span", class_=SEL["score_right"]))
        played = home_goals is not None and away_goals is not None

        matches.append(
            {
                "match_id": RE_MATCH_ID.search(score_link["href"]).group(1),
                "season": season,
                "matchday": matchday,
                "date": match_date,
                "home_team": canonical_team(clubs[0].get_text(strip=True)),
                "away_team": canonical_team(clubs[1].get_text(strip=True)),
                "home_goals": home_goals if played else "",
                "away_goals": away_goals if played else "",
                "status": "played" if played else "scheduled",
            }
        )

    return matches


def collect_season(season: str, use_cache: bool = False) -> List[Dict]:
    """A matchday whose page fussball.de fails to serve is skipped rather than
    losing the whole run; validate() then reports the gap."""
    matches: Dict[str, Dict] = {}
    for matchday, html in fetch_season(season, use_cache):
        if html is None:
            continue
        found = parse_matchday(html, season, matchday)
        logger.info(f"{season} md{matchday}: {len(found)} matches")
        for match in found:
            matches[match["match_id"]] = match
    return sorted(matches.values(), key=lambda m: (m["matchday"], m["date"]))


def validate(matches: List[Dict], season: str) -> None:
    """Raise loudly rather than writing partial data."""
    if not matches:
        raise ValueError(f"No matches parsed for season {season}")

    teams = set()
    for m in matches:
        date.fromisoformat(m["date"])
        if m["home_team"] == m["away_team"]:
            raise ValueError(f"Team plays itself: {m['match_id']}")
        teams.update((m["home_team"], m["away_team"]))
        if m["status"] == "played":
            for side in ("home_goals", "away_goals"):
                if not 0 <= int(m[side]) <= 20:
                    raise ValueError(f"Implausible score in {m['match_id']}: {m[side]}")

    expected = KNOWN_TEAMS.get(season)
    if expected and teams != expected:
        raise ValueError(
            f"Team set mismatch for {season}. "
            f"Unexpected: {sorted(teams - expected)}. Missing: {sorted(expected - teams)}"
        )
    if len(teams) % 2:
        raise ValueError(f"Odd number of teams ({len(teams)}) in {season}")

    # Every team plays every other twice, so every matchday has len(teams)/2
    # matches and the season has len(teams) * (len(teams) - 1) of them.
    per_matchday = len(teams) // 2
    expected_matches = len(teams) * (len(teams) - 1)
    if len(matches) != expected_matches:
        seen = Counter(m["matchday"] for m in matches)
        incomplete = sorted(
            md for md in range(1, expected_matches // per_matchday + 1)
            if seen[md] != per_matchday
        )
        raise ValueError(
            f"{season}: parsed {len(matches)} matches, expected {expected_matches}. "
            f"Incomplete matchdays: {incomplete}. "
            f"Add the missing matches to data/manual_overrides.csv if fussball.de "
            f"cannot serve those pages."
        )

    played = sum(1 for m in matches if m["status"] == "played")
    logger.info(f"Validated {season}: {len(matches)} matches ({played} played), {len(teams)} teams")


def save_matches_csv(matches: List[Dict], csv_path) -> None:
    """Upsert by match_id. Never shrinks an existing file."""
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Dict] = {}
    if csv_path.exists():
        with open(csv_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                existing[row["match_id"]] = row

    before = len(existing)
    for match in matches:
        existing[match["match_id"]] = match
    if len(existing) < before:
        raise ValueError(f"Refusing to shrink {csv_path}: {before} -> {len(existing)}")

    rows = sorted(existing.values(), key=lambda m: (m["season"], m["matchday"], m["date"]))
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Wrote {len(rows)} matches to {csv_path}")
