import logging
import time
import requests
from datetime import datetime, timezone
from app import db
from app.models import Race, Driver, RaceResult, Prediction

OPEN_F1 = 'https://api.openf1.org/v1'
TIMEOUT = 10

logger = logging.getLogger(__name__)


# ── Core HTTP ────────────────────────────────────────────────────────────────

def _get(path, **params):
    """GET an OpenF1 endpoint, return parsed JSON list or raise RuntimeError.
    Retries on transient network errors and on 429 (OpenF1 rate-limits bursts of
    requests); other HTTP errors are raised immediately."""
    query = {k: v for k, v in params.items() if v is not None}
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(f'{OPEN_F1}/{path}', params=query, timeout=TIMEOUT)
            if resp.status_code == 429:
                last_err = RuntimeError('429 Too Many Requests')
                logger.warning('OpenF1 rate-limited (attempt %d/3) for %s %s', attempt + 1, path, query)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            logger.warning('OpenF1 request failed (attempt %d/3) for %s %s: %s', attempt + 1, path, query, e)
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            logger.error('OpenF1 request error for %s %s: %s', path, query, e)
            raise RuntimeError(str(e))
    raise RuntimeError(str(last_err))


# ── Meetings / session helpers ───────────────────────────────────────────────

_MEETINGS_CACHE = {}      # season -> (monotonic_ts, meetings)
_MEETINGS_TTL = 3600


def _race_meetings(season):
    """Return actual race meetings for the season — no testing, no cancellations — sorted by date.
    Cached for an hour: the calendar rarely changes and every session lookup needs it."""
    cached = _MEETINGS_CACHE.get(season)
    if cached and time.monotonic() - cached[0] < _MEETINGS_TTL:
        return cached[1]
    meetings = _get('meetings', year=season)
    result = sorted(
        [m for m in meetings
         if not m.get('is_cancelled')
         and 'testing'    not in m.get('meeting_name', '').lower()
         and 'pre-season' not in m.get('meeting_name', '').lower()],
        key=lambda m: m['date_start'],
    )
    _MEETINGS_CACHE[season] = (time.monotonic(), result)
    return result


def _find_meeting(meetings, round_number, race_date=None):
    """Pick the OpenF1 meeting for a race. Matches by date (race falls within the
    meeting weekend) because OpenF1 can list extra/placeholder meetings that shift
    list positions; falls back to round-number index when no date is available."""
    if race_date:
        for m in meetings:
            start = _parse_dt(m['date_start'])
            if start and 0 <= (race_date - start).total_seconds() <= 5 * 86400:
                return m
    if 1 <= round_number <= len(meetings):
        return meetings[round_number - 1]
    return None


def _find_session(season, round_number, session_type, session_name=None, race_date=None):
    """Return the session dict for a given round number + session type, or None.
    Pass session_name to disambiguate when multiple sessions share a type
    (e.g. both 'Race' and 'Sprint' have session_type='Race' on a sprint weekend;
    'Qualifying' vs 'Sprint Qualifying' both have session_type='Qualifying').
    Sessions are sorted by date_start before picking, since OpenF1 gives no
    ordering guarantee and there can be more than one match per type.
    """
    meetings = _race_meetings(season)
    meeting = _find_meeting(meetings, round_number, race_date)
    if not meeting:
        return None
    meeting_key = meeting['meeting_key']
    sessions = _get('sessions', meeting_key=meeting_key, session_type=session_type)
    if session_name:
        sessions = [s for s in sessions if s.get('session_name') == session_name]
    sessions = sorted(sessions, key=lambda s: s['date_start'])
    return sessions[0] if sessions else None


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

_QUALI_CACHE = {}        # race_id -> (monotonic_ts, rows)
_QUALI_TTL_FOUND = 600   # seconds — final qualifying order doesn't change
_QUALI_TTL_EMPTY = 60    # not available yet (or API error): retry soon, but not on every page view


def fetch_qualifying_top10(race):
    """Cached wrapper: each uncached lookup costs ~4 OpenF1 requests, and the
    predict page calls this on every view and submit."""
    now = time.monotonic()
    cached = _QUALI_CACHE.get(race.id)
    if cached:
        ttl = _QUALI_TTL_FOUND if cached[1] else _QUALI_TTL_EMPTY
        if now - cached[0] < ttl:
            return cached[1]
    rows = _fetch_qualifying_top10(race)
    _QUALI_CACHE[race.id] = (now, rows)
    return rows


def _fetch_qualifying_top10(race):
    """Return up to 10 dicts {pos, name, given, code, team} from qualifying.
    For sprint races uses Sprint Qualifying (shootout); for main races uses Qualifying.
    """
    try:
        session_name = 'Sprint Qualifying' if race.race_type == 'sprint' else 'Qualifying'
        session = _find_session(race.season, race.round_number, 'Qualifying',
                                session_name=session_name, race_date=race.race_date)
        if not session:
            return []
        sk = session['session_key']

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
    """Fetch top 5 finishers from OpenF1, save result, and score predictions.
    Both the main Race and the Sprint share session_type='Race' on OpenF1 —
    they're only distinguished by session_name — so session_name must always
    be passed to avoid picking up the wrong one (see fetch_qualifying_top10)."""
    try:
        session_name = 'Sprint' if race.race_type == 'sprint' else 'Race'
        session = _find_session(race.season, race.round_number, 'Race',
                                session_name=session_name, race_date=race.race_date)
        if not session:
            return False, 'Session not found for this round.'
        if session.get('session_name') != session_name:
            logger.error('Session name mismatch for race %s: expected %s, got %s',
                        race.id, session_name, session.get('session_name'))
            return False, 'Session mismatch — refusing to save wrong result.'
        sk = session['session_key']

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
        logger.error('fetch_race_result failed for race %s (%s): %s', race.id, race.name, e)
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
    _MEETINGS_CACHE.pop(season, None)   # always import from fresh data
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

        # Sprint and main race both have session_type 'Race' on OpenF1; tell them apart by name.
        race_s    = next((s for s in slist if s.get('session_name') == 'Race'),   None)
        sprint_s  = next((s for s in slist if s.get('session_name') == 'Sprint'), None)
        race_date = _parse_dt(race_s['date_start'] if race_s else meeting['date_start'])

        for kind, sess in (('race', race_s), ('sprint', sprint_s)):
            if kind == 'sprint' and not sess:
                continue
            when = _parse_dt(sess['date_start']) if sess else race_date
            # Match by type + date, not round number: OpenF1 can insert meetings that
            # shift numbering, and re-importing must not create duplicates.
            candidates = [r for r in Race.query.filter_by(season=season, race_type=kind).all()
                          if r.race_date and when and abs((r.race_date - when).total_seconds()) < 3 * 86400]
            existing = min(candidates, key=lambda r: abs((r.race_date - when).total_seconds()), default=None)
            if existing:
                existing.round_number = round_num
                continue
            name = meeting['meeting_name']
            db.session.add(Race(
                name=name.replace('Grand Prix', 'Sprint') if kind == 'sprint' else name,
                round_number=round_num,
                season=season,
                race_date=when,
                race_type=kind,
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
