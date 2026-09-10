#!/usr/bin/env python
"""Entrypoint: scrape -> parse -> rate. Run once after each matchday."""

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import (  # noqa: E402
    FORM_WINDOW,
    MANUAL_OVERRIDES_CSV,
    MATCHES_CSV,
    SEASON_CURRENT,
    STAFFEL_IDS,
)
from parse import collect_season, save_matches_csv, validate  # noqa: E402
from report import build_table, write_report  # noqa: E402

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
    """The table the page shows, sorted by current form.

    Built by report.build_table() rather than replayed a second time here, so the
    terminal and docs/index.html cannot drift apart - they are the same numbers in
    the same order.
    """
    rows = [r for r in load_season(season) if r["status"] == "played"]
    if not rows:
        return []
    table, _ = build_table(rows)
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

    print(f"\nFormtabelle {args.season}  (letzte {FORM_WINDOW} Spieltage)")
    print(f"{'#':>3}  {'Team':<32}{'Form':>7}{'Saison':>8}{'Sp':>4}{'Tab':>5}")
    for i, t in enumerate(table, 1):
        # Same chip as the page: how far the official table sits from this rank.
        diff = t["position"] - i
        note = f"  {diff:+d}" if diff else ""
        print(f"{i:>3}. {t['team']:<32}{t['form']:>7.1f}{t['power']:>8.1f}"
              f"{t['matches']:>4}{t['position']:>5}{note}")

    played = [r for r in load_season(args.season) if r["status"] == "played"]
    logger.info(f"Wrote {write_report(args.season, played)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
