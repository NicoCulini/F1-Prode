"""
auto_results.py — Run this as a PythonAnywhere scheduled task (hourly).

Checks for any race that:
  - Has passed its start time + 2 hours (enough time for race to finish)
  - Is not yet marked completed
  - Has position data available in OpenF1

If found, fetches the result, scores predictions, and marks the race done.

PythonAnywhere setup:
  Dashboard → Tasks → Add task → Hourly
  Command: python /home/YOUR_USERNAME/F1-Prode/auto_results.py
"""

import sys
import os

# ── Path setup (must match your PythonAnywhere username) ────────────────────
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# ── Bootstrap Flask app ──────────────────────────────────────────────────────
from app import create_app, db
from app.models import Race
from app.scraper import fetch_race_result
from config import Config
from datetime import datetime, timedelta

app = create_app()

RACE_BUFFER_HOURS = 2   # how long after race_date we expect the race to be done
RESULT_WINDOW_HOURS = 8 # don't try to auto-fetch results older than this


def run():
    with app.app_context():
        now = datetime.utcnow()

        # Races that should be done but aren't marked complete yet
        candidates = (Race.query
                      .filter_by(season=Config.CURRENT_SEASON, is_completed=False)
                      .filter(Race.race_date != None)
                      .filter(Race.race_date + timedelta(hours=RACE_BUFFER_HOURS) <= now)
                      .filter(Race.race_date + timedelta(hours=RACE_BUFFER_HOURS + RESULT_WINDOW_HOURS) >= now)
                      .order_by(Race.race_date)
                      .all())

        if not candidates:
            print(f'[{now:%Y-%m-%d %H:%M}] No races pending auto-result.')
            return

        for race in candidates:
            print(f'[{now:%Y-%m-%d %H:%M}] Attempting result fetch for: {race.name} ({race.race_type})')
            ok, msg = fetch_race_result(race)
            print(f'  → {"✓" if ok else "✗"} {msg}')


if __name__ == '__main__':
    run()
