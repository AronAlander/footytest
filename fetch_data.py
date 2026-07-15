"""Fetch Allsvenskan and Serie A data from TheSportsDB (free test key, no signup).

Uses only the Python standard library. Saves raw JSON responses to data/
and prints a short summary of what was fetched.

Usage:
    python fetch_data.py
"""

import json
import urllib.request
from datetime import date
from pathlib import Path

API_KEY = "123"  # TheSportsDB public test key, fine for development
BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"

LEAGUES = {
    "allsvenskan": {
        "id": "4347",
        # Allsvenskan runs over a calendar year
        "season": str(date.today().year),
    },
    "serie_a": {
        "id": "4332",
        # Serie A runs autumn-spring
        "season": "2025-2026",
    },
}

ENDPOINTS = {
    "table": "lookuptable.php?l={id}&s={season}",
    "past_events": "eventspastleague.php?id={id}",
    "next_events": "eventsnextleague.php?id={id}",
    "season_events": "eventsseason.php?id={id}&s={season}",
}

DATA_DIR = Path(__file__).parent / "data"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analytics/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    for league_name, league in LEAGUES.items():
        print(f"\n=== {league_name} (season {league['season']}) ===")
        for endpoint_name, endpoint_template in ENDPOINTS.items():
            url = f"{BASE_URL}/{endpoint_template.format(**league)}"
            try:
                payload = fetch_json(url)
            except Exception as error:
                print(f"  {endpoint_name}: FAILED ({error})")
                continue

            out_path = DATA_DIR / f"{league_name}_{endpoint_name}.json"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            # Responses wrap their list under varying keys (table, events, ...)
            first_list = next((v for v in payload.values() if isinstance(v, list)), None)
            count = len(first_list) if first_list else 0
            print(f"  {endpoint_name}: {count} records -> {out_path.name}")


if __name__ == "__main__":
    main()
