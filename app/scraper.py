import requests
from app import db
from app.models import Race, Driver, RaceResult, Prediction


F1_API = 'https://api.jolpi.ca/ergast/f1'
TIMEOUT = 10


def fetch_season_drivers(season):
    """Fetch and store all drivers for a given season."""
    url = f'{F1_API}/{season}/drivers.json?limit=30'
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        drivers_data = data['MRData']['DriverTable']['Drivers']
    except Exception as e:
        return False, f'API error: {e}'

    Driver.query.filter_by(season=season).delete()
    for d in drivers_data:
        driver = Driver(
            code=d.get('code', ''),
            family_name=d['familyName'],
            given_name=d['givenName'],
            season=season,
        )
        db.session.add(driver)

    try:
        db.session.commit()
        return True, f'Imported {len(drivers_data)} drivers.'
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def fetch_season_schedule(season):
    """Fetch race schedule and upsert Race rows, including sprints. Auto-detects H1/H2 split."""
    url = f'{F1_API}/{season}.json'
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        races_data = data['MRData']['RaceTable']['Races']
    except Exception as e:
        return False, f'API error: {e}'

    # Split into equal halves by round count (all scheduled rounds, raced or not)
    all_rounds = sorted(int(r['round']) for r in races_data)
    mid = len(all_rounds) // 2
    h1_rounds = set(all_rounds[:mid])

    def half(round_num):
        return 1 if round_num in h1_rounds else 2

    added = 0
    for r in races_data:
        round_num = int(r['round'])

        if not Race.query.filter_by(season=season, round_number=round_num, race_type='race').first():
            db.session.add(Race(
                name=r['raceName'],
                round_number=round_num,
                season=season,
                race_date=_parse_date(r.get('date'), r.get('time')),
                race_type='race',
                season_half=half(round_num),
                is_completed=False,
                predictions_open=True,
            ))
            added += 1

        if 'Sprint' in r and not Race.query.filter_by(season=season, round_number=round_num, race_type='sprint').first():
            db.session.add(Race(
                name=r['raceName'].replace('Grand Prix', 'Sprint'),
                round_number=round_num,
                season=season,
                race_date=_parse_date(r['Sprint'].get('date'), r['Sprint'].get('time')),
                race_type='sprint',
                season_half=half(round_num),
                is_completed=False,
                predictions_open=True,
            ))
            added += 1

    try:
        db.session.commit()
        h1_count = sum(1 for r in races_data if int(r['round']) in h1_rounds)
        return True, f'Imported {added} new races. H1: {h1_count} rounds, H2: {len(all_rounds) - h1_count} rounds.'
    except Exception as e:
        db.session.rollback()
        return False, str(e)



def _parse_date(date_str, time_str):
    from datetime import datetime
    if not date_str:
        return None
    try:
        dt_str = date_str + ' ' + (time_str or '00:00:00Z').rstrip('Z')
        return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
    except Exception:
        return None


def fetch_race_result(race):
    """Fetch result for a completed race/sprint and update predictions."""
    if race.race_type == 'sprint':
        url = f'{F1_API}/{race.season}/{race.round_number}/sprint.json'
        result_key = 'SprintResults'
    else:
        url = f'{F1_API}/{race.season}/{race.round_number}/results.json'
        result_key = 'Results'

    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        races = data['MRData']['RaceTable']['Races']
        if not races:
            return False, 'No results available yet.'
        results = races[0][result_key]
    except Exception as e:
        return False, f'API error: {e}'

    top5 = [r['Driver']['familyName'] for r in results[:5]]
    while len(top5) < 5:
        top5.append(None)

    existing = RaceResult.query.filter_by(race_id=race.id).first()
    if existing:
        existing.pos1, existing.pos2, existing.pos3 = top5[0], top5[1], top5[2]
        existing.pos4, existing.pos5 = top5[3], top5[4]
        from datetime import datetime as dt
        existing.fetched_at = dt.utcnow()
    else:
        result = RaceResult(
            race_id=race.id,
            pos1=top5[0], pos2=top5[1], pos3=top5[2],
            pos4=top5[3], pos5=top5[4],
        )
        db.session.add(result)

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


def fetch_qualifying_top10(race):
    """Return up to 10 dicts {pos, name, given, code, team} from main qualifying.
    Jolpica has no sprint shootout endpoint, so both race and sprint use qualifying."""
    url = f'{F1_API}/{race.season}/{race.round_number}/qualifying.json'
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        races = data['MRData']['RaceTable']['Races']
        if not races:
            return []
        results = races[0]['QualifyingResults']
    except Exception:
        return []

    return [
        {
            'pos': r['position'],
            'name': r['Driver']['familyName'],
            'given': r['Driver']['givenName'],
            'code': r['Driver'].get('code', ''),
            'team': r['Constructor']['name'],
        }
        for r in results[:10]
    ]


def _score_predictions(race):
    for pred in Prediction.query.filter_by(race_id=race.id).all():
        pred.score = pred.calculate_score()
