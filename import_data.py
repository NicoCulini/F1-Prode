"""One-shot import script: create users + predictions from the spreadsheet."""
import sys, re
sys.path.insert(0, '.')

from app import create_app, db
from app.models import User, Race, Prediction, RaceResult, Driver
from app.scraper import fetch_season_schedule, fetch_season_drivers, fetch_race_result
from werkzeug.security import generate_password_hash

# ── Driver code → family name ────────────────────────────────
DRIVER_MAP = {
    'RUS': 'Russell',   'LEC': 'Leclerc',    'HAD': 'Hadjar',
    'ANT': 'Antonelli', 'PIA': 'Piastri',    'HAM': 'Hamilton',
    'VER': 'Verstappen','NOR': 'Norris',      'COL': 'Colapinto',
    'SAI': 'Sainz',     'ALO': 'Alonso',     'STR': 'Stroll',
    'GAS': 'Gasly',     'ALB': 'Albon',      'HUL': 'Hulkenberg',
    'TSU': 'Tsunoda',   'OCO': 'Ocon',       'BEA': 'Bearman',
    'LAW': 'Lawson',    'BOR': 'Bortoleto',  'DOO': 'Doohan',
}

def parse_picks(cell):
    s = str(cell).strip()
    if s in ('nan', 'DNS', 'NaN', '', 'None'):
        return None
    picks = []
    for line in s.split('\n'):
        m = re.search(r'P\d+\s*[-:]\s*([A-Z]{2,4})', line.strip())
        if m:
            code = m.group(1)
            picks.append(DRIVER_MAP.get(code, code))
    return picks if len(picks) >= 2 else None

# ── Hardcoded predictions from spreadsheet ───────────────────
# Users in order: Niqoh, Siro, Sinpi, Nake, Gonza, Trt, Ghili, Agape, Maira
USERS = ['Niqoh', 'Siro', 'Sinpi', 'Nake', 'Gonza', 'Trt', 'Ghili', 'Agape', 'Maira']

RACE_PREDICTIONS = {
    'Australia': [
        'P1- RUS\nP2- LEC\nP3- HAD\nP4- ANT\nP5- PIA',
        'P1- RUS\nP2- LEC\nP3- ANT\nP4- NOR\nP5- HAD',
        'P1- RUS\nP2- ANT\nP3- PIA\nP4- LEC\nP5- HAM',
        'P1- RUS\nP2- LEC\nP3- HAM\nP4- VER\nP5- HAD',
        'DNS',
        'P1- RUS\nP2- PIA\nP3- LEC\nP4- NOR\nP5- HAD',
        'P1- ANT\nP2- RUS\nP3- LEC\nP4- HAD\nP5- HAM',
        'P1- RUS\nP2- LEC\nP3- ANT\nP4- HAD\nP5- PIA',
        'P1- RUS\nP2- NOR\nP3- VER\nP4- HAM\nP5- LEC',
    ],
    'China': [
        'P1- RUS\nP2- ANT\nP3- LEC\nP4- HAM\nP5- NOR',
        'P1- LEC\nP2- RUS\nP3- HAM\nP4- PIA\nP5- VER',
        'P1- HAM\nP2- RUS\nP3- LEC\nP4- ANT\nP5- NOR',
        'P1- RUS\nP2- HAM\nP3- LEC\nP4- ANT\nP5- VER',
        'P1- RUS\nP2- LEC\nP3- HAM\nP4- VER\nP5- ANT',
        'P1- RUS\nP2- ANT\nP3- LEC\nP4- PIA\nP5- HAM',
        'P1- ANT\nP2- LEC\nP3- RUS\nP4- HAM\nP5- NOR',
        'P1- HAM\nP2- RUS\nP3- ANT\nP4- LEC\nP5- VER',
        'P1- RUS\nP2- HAM\nP3- LEC\nP4- NOR\nP5- VER',
    ],
    'Japan': [
        'P1- ANT\nP2- RUS\nP3- LEC\nP4- HAM\nP5- PIA',
        'P1- RUS\nP2- LEC\nP3- ANT\nP4- PIA\nP5- HAM',
        'P1- ANT\nP2- PIA\nP3- RUS\nP4- HAM\nP5- LEC',
        'P1- ANT\nP2- RUS\nP3- LEC\nP4- HAM\nP5- PIA',
        'P1- RUS\nP2- PIA\nP3- LEC\nP4- ANT\nP5- HAM',
        'DNS',
        'P1- ANT\nP2- RUS\nP3- LEC\nP4- HAM\nP5- NOR',
        'P1- ANT\nP2- RUS\nP3- LEC\nP4- HAM\nP5- PIA',
        'P1- LEC\nP2- ANT\nP3- HAM\nP4- VER\nP5- PIA',
    ],
    'Miami': [
        'P1- VER\nP2- NOR\nP3- LEC\nP4- ANT\nP5- RUS',
        'P1- LEC\nP2- VER\nP3- ANT\nP4- NOR\nP5- HAM',
        'P1- ANT\nP2- VER\nP3- LEC\nP4- NOR\nP5- HAM',
        'P1- ANT\nP2- LEC\nP3- VER\nP4- HAM\nP5- COL',
        'P1- NOR\nP2- VER\nP3- LEC\nP4- RUS\nP5- PIA',
        'DNS',
        'P1- LEC\nP2- ANT\nP3- NOR\nP4- VER\nP5- RUS',
        'P1- VER\nP2- LEC\nP3- ANT\nP4- RUS\nP5- NOR',
        'P1- LEC\nP2- ANT\nP3- RUS\nP4- VER\nP5- NOR',
    ],
    'Canada': [
        'P1- ANT\nP2- RUS\nP3- NOR\nP4- PIA\nP5- HAM',
        'P1- RUS\nP2- NOR\nP3- ANT\nP4- PIA\nP5- VER',
        'P1- ANT\nP2- RUS\nP3- HAM\nP4- NOR\nP5- PIA',
        'P1- ANT\nP2- NOR\nP3- RUS\nP4- PIA\nP5- HAM',
        'P1- ANT\nP2- RUS\nP3- NOR\nP4- PIA\nP5- LEC',
        'DNS',
        'P1- ANT\nP2- RUS\nP3- NOR\nP4- VER\nP5- PIA',
        'P1- ANT\nP2- RUS\nP3- NOR\nP4- PIA\nP5- VER',
        'P1- NOR\nP2- RUS\nP3- PIA\nP4- ANT\nP5- VER',
    ],
}

SPRINT_PREDICTIONS = {
    'China': [
        'P1-RUS\nP2- ANT\nP3- HAM',
        'P1-RUS\nP2- ANT\nP3- HAM',
        'P1- RUS\nP2- HAM\nP3- ANT',
        'P1-RUS\nP2- ANT\nP3- HAM',
        'DNS',
        'P1-RUS\nP2- ANT\nP3- HAM',
        'P1-RUS\nP2- ANT\nP3- HAM',
        'P1-RUS\nP2- ANT\nP3- HAM',
        'P1- HAM\nP2- PIA\nP3- ANT',
    ],
    'Miami': [
        'P1- NOR\nP2- PIA\nP3- ANT',
        'P1- ANT\nP2- NOR\nP3- LEC',
        'P1- PIA\nP2- ANT\nP3- LEC',
        'P1-ANT\nP2-LEC\nP3-VER',
        'P1- ANT\nP2- NOR\nP3- LEC',
        'DNS',
        'P1- ANT\nP2- LEC\nP3- NOR',
        'P1-ANT\nP2-LEC\nP3-VER',
        'P1- NOR\nP2- LEC\nP3- ANT',
    ],
    'Canada': [
        'P1- RUS\nP2- NOR\nP3- ANT',
        'P1- RUS\nP2- NOR\nP3- PIA',
        'P1- RUS\nP2- ANT\nP3- HAM',
        'P1- RUS\nP2- NOR\nP3- ANT',
        'DNS',
        'DNS',
        'P1- ANT\nP2- RUS\nP3- NOR',
        'P1- ANT\nP2- NOR\nP3- RUS',
        'P1- ANT\nP2- RUS\nP3- VER',
    ],
}

# Name keywords to match against Jolpica race names
RACE_KEYWORDS = {
    'Australia': ['australia', 'australian'],
    'China':     ['china', 'chinese'],
    'Japan':     ['japan', 'japanese'],
    'Miami':     ['miami'],
    'Canada':    ['canada', 'canadian'],
}

app = create_app()

with app.app_context():
    # 1. Import schedule + drivers
    print('--- Importing 2026 schedule...')
    ok, msg = fetch_season_schedule(2026)
    print(msg)
    ok, msg = fetch_season_drivers(2026)
    print(msg)

    # 2. Create users (skip Nake = already exists)
    print('--- Creating users...')
    for username in USERS:
        if not User.query.filter_by(username=username).first():
            db.session.add(User(username=username, password_hash=generate_password_hash('123')))
            print(f'  Created: {username}')
        else:
            print(f'  Exists:  {username}')
    db.session.commit()

    # 3. Helper to find race by keyword
    def find_race(keywords, race_type='race'):
        for race in Race.query.filter_by(season=2026, race_type=race_type).all():
            for kw in keywords:
                if kw in race.name.lower():
                    return race
        return None

    # 4. Insert predictions
    print('--- Inserting predictions...')
    for race_name, preds in RACE_PREDICTIONS.items():
        race = find_race(RACE_KEYWORDS[race_name], 'race')
        if not race:
            print(f'  RACE NOT FOUND: {race_name}')
            continue
        for username, cell in zip(USERS, preds):
            picks = parse_picks(cell)
            if not picks:
                print(f'  DNS: {username} / {race_name}')
                continue
            user = User.query.filter_by(username=username).first()
            if not user:
                continue
            if Prediction.query.filter_by(user_id=user.id, race_id=race.id).first():
                continue
            while len(picks) < 5:
                picks.append(None)
            db.session.add(Prediction(
                user_id=user.id, race_id=race.id,
                pos1=picks[0], pos2=picks[1], pos3=picks[2],
                pos4=picks[3], pos5=picks[4],
            ))
        print(f'  Predictions added for {race_name} race (Round {race.round_number})')

    for sprint_name, preds in SPRINT_PREDICTIONS.items():
        race = find_race(RACE_KEYWORDS[sprint_name], 'sprint')
        if not race:
            print(f'  SPRINT NOT FOUND: {sprint_name} — will try to add manually')
            # Find the main race and create a sprint entry
            main = find_race(RACE_KEYWORDS[sprint_name], 'race')
            if main:
                race = Race(
                    name=main.name.replace('Grand Prix', 'Sprint'),
                    round_number=main.round_number,
                    season=2026,
                    race_date=main.race_date,
                    race_type='sprint',
                    season_half=main.season_half,
                    is_completed=False,
                    predictions_open=True,
                )
                db.session.add(race)
                db.session.flush()
                print(f'  Auto-created sprint for {sprint_name}')
            else:
                continue
        for username, cell in zip(USERS, preds):
            picks = parse_picks(cell)
            if not picks:
                print(f'  DNS: {username} / {sprint_name} sprint')
                continue
            user = User.query.filter_by(username=username).first()
            if not user:
                continue
            if Prediction.query.filter_by(user_id=user.id, race_id=race.id).first():
                continue
            while len(picks) < 3:
                picks.append(None)
            db.session.add(Prediction(
                user_id=user.id, race_id=race.id,
                pos1=picks[0], pos2=picks[1], pos3=picks[2],
            ))
        print(f'  Predictions added for {sprint_name} sprint (Round {race.round_number})')

    db.session.commit()
    print('--- Predictions committed.')

    # 5. Fetch results + score all completed races
    print('--- Fetching results from Jolpica API...')
    completed_names = list(RACE_PREDICTIONS.keys()) + list(SPRINT_PREDICTIONS.keys())
    for race_name, race_type in [(n, 'race') for n in RACE_PREDICTIONS] + [(n, 'sprint') for n in SPRINT_PREDICTIONS]:
        race = find_race(RACE_KEYWORDS[race_name], race_type)
        if not race:
            continue
        ok, msg = fetch_race_result(race)
        print(f'  {race_name} {race_type}: {msg}')

    print('--- Done.')
    print()
    print('=== FINAL SCORES ===')
    from app.models import User, Prediction
    from sqlalchemy import func
    users = User.query.order_by(User.username).all()
    totals = []
    for u in users:
        total = sum(p.score for p in Prediction.query.filter_by(user_id=u.id).all())
        totals.append((u.username, total))
    for name, pts in sorted(totals, key=lambda x: -x[1]):
        print(f'  {name}: {pts} pts')
