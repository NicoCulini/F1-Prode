import requests
from datetime import datetime, timezone
from app import db
from app.models import Race, Driver, RaceResult, Prediction

OPEN_F1 = 'https://api.openf1.org/v1'
TIMEOUT = 10


# ── Core HTTP ────────────────────────────────────────────────────────────────

def _get(path, **params):
    """GET an OpenF1 endpoint, return parsed JSON list or raise RuntimeError."""
    try:
        resp = requests.get(
            f'{OPEN_F1}/{path}',
            params={k: v for k, v in params.items() if v is not None},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise RuntimeError(str(e))


# ── Meetings / session helpers ───────────────────────────────────────────────

def _race_meetings(season):
    """Return actual race meetings for the season — no testing, no cancellations — sorted by date."""
    meetings = _get('meetings', year=season)
    return sorted(
        [m for m in meetings
         if not m.get('is_cancelled')
         and 'testing'    not in m.get('meeting_name', '').lower()
         and 'pre-season' not in m.get('meeting_name', '').lower()],
        key=lambda m: m['date_start'],
    )


def _session_key(season, round_number, session_type, session_name=None):
    """Return the session_key for a given round number + session type, or None.
    Pass session_name to disambiguate when multiple sessions share a type
    (e.g. 'Qualifying' vs 'Sprint Qualifying' both have session_type='Qualifying').
    """
    meetings = _race_meetings(season)
    if round_number < 1 or round_number > len(meetings):
        return None
    meeting_key = meetings[round_number - 1]['meeting_key']
    sessions = _get('sessions', meeting_key=meeting_key, session_type=session_type)
    if session_name:
        sessions = [s for s in sessions if s.get('session_name') == session_name]
    return sessions[0]['session_key'] if sessions else None


def _final_positions(session_key):
    """
    Return {driver_number: position} using the most recent position record per driver.
    Works for both race and qualifying sessions.
    """
    records = _get('position', session_key=session_key)
    latest = {}
    for r in records:
        dn = r['driver_number']
        if dn not in latest or r['date'] > latest[dn]['date']:
            latest[dn] = r
    return {dn: r['position'] for dn, r in latest.items()}


# ── Public functions ─────────────────────────────────────────────────────────

def fetch_qualifying_top10(race):
    """Return up to 10 dicts {pos, name, given, code, team} from qualifying.
    For sprint races uses Sprint Qualifying (shootout); for main races uses Qualifying.
    """
    try:
        session_name = 'Sprint Qualifying' if race.race_type == 'sprint' else 'Qualifying'
        sk = _session_key(race.season, race.round_number, 'Qualifying',
                          session_name=session_name)
        if not sk:
            return []

        positions = _final_positions(sk)
        if not positions:
            return []

        driver_map = {d['driver_number']: d
                      for d in _get('drivers', session_key=sk)}

        top10 = sorted(positions.items(), key=lambda x: x[1])[:10]
        return [
            {
                'pos':   pos,
                'name':  driver_map.get(dn, {}).get('last_name', str(dn)),
                'given': driver_map.get(dn, {}).get('first_name', ''),
                'code':  driver_map.get(dn, {}).get('name_acronym', ''),
                'team':  driver_map.get(dn, {}).get('team_name', ''),
            }
            for dn, pos in top10
        ]
    except Exception:
        return []


def fetch_race_result(race):
    """Fetch top 5 finishers from OpenF1, save result, and score predictions."""
    try:
        session_type = 'Sprint' if race.race_type == 'sprint' else 'Race'
        sk = _session_key(race.season, race.round_number, session_type)
        if not sk:
            return False, 'Session not found for this round.'

        positions = _final_positions(sk)
        if not positions:
            return False, 'No position data available yet.'

        driver_map = {d['driver_number']: d
                      for d in _get('drivers', session_key=sk)}

        top5_entries = sorted(positions.items(), key=lambda x: x[1])[:5]
        top5 = [driver_map.get(dn, {}).get('last_name', str(dn))
                for dn, _ in top5_entries]
        while len(top5) < 5:
            top5.append(None)

    except Exception as e:
        return False, f'API error: {e}'

    existing = RaceResult.query.filter_by(race_id=race.id).first()
    if existing:
        existing.pos1, existing.pos2, existing.pos3 = top5[0], top5[1], top5[2]
        existing.pos4, existing.pos5 = top5[3], top5[4]
        existing.fetched_at = datetime.utcnow()
    else:
        db.session.add(RaceResult(
            race_id=race.id,
            pos1=top5[0], pos2=top5[1], pos3=top5[2],
            pos4=top5[3], pos5=top5[4],
        ))

    db.session.flush()
    _score_predictions(race)
    race.is_completed = True
    race.predictions_open = False

    try:
        db.session.commit()
        return True, f'Results saved. Top 5: {", ".join(t for t in top5 if t)}'
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def fetch_season_drivers(season):
    """Fetch drivers from the first race session of the season."""
    try:
        meetings = _race_meetings(season)
        if not meetings:
            return False, 'No race meetings found for this season.'
        sessions = _get('sessions', meeting_key=meetings[0]['meeting_key'],
                        session_type='Race')
        if not sessions:
            return False, 'No race session found.'
        drivers_data = _get('drivers', session_key=sessions[0]['session_key'])
    except Exception as e:
        return False, f'API error: {e}'

    Driver.query.filter_by(season=season).delete()
    for d in drivers_data:
        db.session.add(Driver(
            code=d.get('name_acronym', ''),
            family_name=d.get('last_name', ''),
            given_name=d.get('first_name', ''),
            team=d.get('team_name', ''),
            season=season,
        ))
    try:
        db.session.commit()
        return True, f'Imported {len(drivers_data)} drivers.'
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def fetch_season_schedule(season):
    """Fetch and upsert Race rows from OpenF1 meetings + sessions."""
    try:
        meetings = _race_meetings(season)
        if not meetings:
            return False, 'No race meetings found.'
        all_sessions = _get('sessions', year=season)
    except Exception as e:
        return False, f'API error: {e}'

    by_meeting = {}
    for s in all_sessions:
        by_meeting.setdefault(s['meeting_key'], []).append(s)

    mid = len(meetings) // 2
    added = 0

    for i, meeting in enumerate(meetings):
        round_num   = i + 1
        season_half = 1 if i < mid else 2
        mk          = meeting['meeting_key']
        slist       = by_meeting.get(mk, [])

        race_s    = next((s for s in slist if s['session_type'] == 'Race'),   None)
        sprint_s  = next((s for s in slist if s['session_type'] == 'Sprint'), None)
        race_date = _parse_dt(race_s['date_start'] if race_s else meeting['date_start'])

        if not Race.query.filter_by(season=season, round_number=round_num, race_type='race').first():
            db.session.add(Race(
                name=meeting['meeting_name'],
                round_number=round_num,
                season=season,
                race_date=race_date,
                race_type='race',
                season_half=season_half,
                is_completed=False,
                predictions_open=True,
            ))
            added += 1

        if sprint_s and not Race.query.filter_by(season=season, round_number=round_num, race_type='sprint').first():
            db.session.add(Race(
                name=meeting['meeting_name'].replace('Grand Prix', 'Sprint'),
                round_number=round_num,
                season=season,
                race_date=_parse_dt(sprint_s['date_start']),
                race_type='sprint',
                season_half=season_half,
                is_completed=False,
                predictions_open=True,
            ))
            added += 1

    try:
        db.session.commit()
        h1 = mid
        return True, f'Imported {added} new races. H1: {h1} rounds, H2: {len(meetings) - h1} rounds.'
    except Exception as e:
        db.session.rollback()
        return False, str(e)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_dt(date_str):
    """Parse ISO 8601 string to naive UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _score_predictions(race):
    for pred in Prediction.query.filter_by(race_id=race.id).all():
        pred.score = pred.calculate_score()
