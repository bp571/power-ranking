#!/usr/bin/env python
"""Entrypoint: scrape -> parse -> rate. Run once after each matchday."""

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import MANUAL_OVERRIDES_CSV, MATCHES_CSV, SEASON_CURRENT, STAFFEL_IDS  # noqa: E402
from parse import collect_season, save_matches_csv, validate  # noqa: E402
from rating import EloRating  # noqa: E402
from report import write_report  # noqa: E402
from score import normalize_to_power_score  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def apply_overrides(matches, season: str):
    """Merge hand-entered rows for matches fussball.de cannot serve.
    Same columns as matches.csv; a row wins over the scraped one."""
    path = Path(MANUAL_OVERRIDES_CSV)
    if not path.exists():
        return matches

    by_id = {m["match_id"]: m for m in matches}
    added = 0
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["season"] != season:
                continue
            row["matchday"] = int(row["matchday"])
            if row["status"] == "played":
                row["home_goals"] = int(row["home_goals"])
                row["away_goals"] = int(row["away_goals"])
            by_id[row["match_id"]] = row
            added += 1

    if added:
        logger.info(f"Applied {added} manual override(s) from {path.name}")
    return sorted(by_id.values(), key=lambda m: (m["matchday"], m["date"]))


def load_season(season: str):
    with open(MATCHES_CSV, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["season"] == season]


def rank(season: str):
    """Replay the season chronologically and return the ranking."""
    rows = [r for r in load_season(season) if r["status"] == "played"]
    teams = sorted({r[side] for r in rows for side in ("home_team", "away_team")})

    elo = EloRating()
    elo.initialize_teams(teams)
    for r in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        elo.update_from_match(
            r["home_team"],
            r["away_team"],
            int(r["home_goals"]),
            int(r["away_goals"]),
            int(r["matchday"]),
        )

    ratings = elo.get_ratings()
    table = [
        {
            "team": team,
            "rating": ratings[team],
            "matches": len(elo.rating_history[team]) - 1,
            "power": normalize_to_power_score(ratings[team], len(elo.rating_history[team]) - 1),
        }
        for team in teams
    ]
    table.sort(key=lambda t: t["power"], reverse=True)
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default=SEASON_CURRENT, choices=sorted(STAFFEL_IDS))
    parser.add_argument(
        "--cached",
        action="store_true",
        help="parse the saved pages in data/raw instead of re-fetching",
    )
    args = parser.parse_args()

    matches = collect_season(args.season, use_cache=args.cached)
    matches = apply_overrides(matches, args.season)
    validate(matches, args.season)
    save_matches_csv(matches, MATCHES_CSV)

    table = rank(args.season)
    if not table:
        logger.info(f"No played matches yet in {args.season} - nothing to rank.")
        return 0

    print(f"\nPower Ranking {args.season}")
    print(f"{'#':>3}  {'Team':<32}{'Power':>7}{'Elo':>8}{'Sp':>4}")
    for i, t in enumerate(table, 1):
        print(f"{i:>3}. {t['team']:<32}{t['power']:>7.1f}{t['rating']:>8.0f}{t['matches']:>4}")

    played = [r for r in load_season(args.season) if r["status"] == "played"]
    logger.info(f"Wrote {write_report(args.season, played)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
