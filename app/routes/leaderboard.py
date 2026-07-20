from datetime import datetime, timedelta
# pyrefly: ignore [missing-import]
from flask import Blueprint, render_template, request
from app.models import User, Race, Prediction, RaceResult, Setting
from app.scraper import fetch_live_positions
from config import Config

LIVE_WINDOW_HOURS = {'race': 3, 'sprint': 1.5}


def _current_half():
    """Return 'h1' or 'h2' based on the next upcoming race's season_half, else 'overall'."""
    next_race = (Race.query
                 .filter_by(season=Config.CURRENT_SEASON, is_completed=False)
                 .filter(Race.race_date >= datetime.utcnow())
                 .order_by(Race.race_date)
                 .first())
    if next_race:
        return f'h{next_race.season_half}'
    return 'overall'


def _live_race():
    """Return the Race currently on track (started, not yet expected to be over,
    not marked completed), or None."""
    now = datetime.utcnow()
    candidates = (Race.query
                  .filter_by(season=Config.CURRENT_SEASON, is_completed=False)
                  .filter(Race.race_date != None)
                  .filter(Race.race_date <= now)
                  .order_by(Race.race_date)
                  .all())
    for race in candidates:
        window = timedelta(hours=LIVE_WINDOW_HOURS.get(race.race_type, 3))
        if now <= race.race_date + window:
            return race
    return None


leaderboard_bp = Blueprint('leaderboard', __name__)


def _build(season_half=None, race_type=None, live_race=None, live_positions=None):
    """Return sorted rows with per-position hit counts and total points.
    If live_race/live_positions are given and match this scope's filters, its
    provisional points are folded into the totals on top of completed races —
    nothing is written to the DB, this is purely for display."""
    q = Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True)
    if season_half is not None:
        q = q.filter_by(season_half=season_half)
    if race_type is not None:
        q = q.filter_by(race_type=race_type)
    races = q.order_by(Race.race_date).all()

    live_applies = (
        live_race is not None and live_positions
        and (season_half is None or live_race.season_half == season_half)
        and (race_type is None or live_race.race_type == race_type)
    )

    users = User.query.order_by(User.username).all()
    rows = []
    for user in users:
        total        = 0
        hits         = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        race_hits    = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        sprint_hits  = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for race in races:
            pred   = Prediction.query.filter_by(user_id=user.id, race_id=race.id).first()
            result = RaceResult.query.filter_by(race_id=race.id).first()
            if not pred or not result:
                continue
            plist = [pred.pos1, pred.pos2, pred.pos3, pred.pos4, pred.pos5]
            rlist = [result.pos1, result.pos2, result.pos3, result.pos4, result.pos5]
            for i in range(race.max_positions):
                if plist[i] and rlist[i] and plist[i].lower() == rlist[i].lower():
                    hits[i + 1] += 1
                    total += race.position_points[i]
                    if race.race_type == 'sprint':
                        sprint_hits[i + 1] += 1
                    else:
                        race_hits[i + 1] += 1

        if live_applies:
            pred = Prediction.query.filter_by(user_id=user.id, race_id=live_race.id).first()
            if pred:
                plist = [pred.pos1, pred.pos2, pred.pos3, pred.pos4, pred.pos5]
                for i in range(live_race.max_positions):
                    if (i < len(live_positions) and plist[i] and live_positions[i]
                            and plist[i].lower() == live_positions[i].lower()):
                        hits[i + 1] += 1
                        total += live_race.position_points[i]
                        if live_race.race_type == 'sprint':
                            sprint_hits[i + 1] += 1
                        else:
                            race_hits[i + 1] += 1

        rows.append({'user': user, 'total': total, 'hits': hits,
                     'race_hits': race_hits, 'sprint_hits': sprint_hits,
                     'total_hits': sum(hits.values())})

    rows.sort(key=lambda x: (-x['total'], x['user'].username))
    for i, row in enumerate(rows):
        row['rank'] = i + 1
    return rows


def _tapia_overall(h1_rows, h2_rows):
    h1_map = {r['user'].username: r for r in h1_rows}
    h2_map = {r['user'].username: r for r in h2_rows}
    combined = []
    for row in h1_rows:
        u = row['user'].username
        h1_total = h1_map[u]['total']
        h2_total = h2_map[u]['total'] if u in h2_map else 0
        hits = {p: h1_map[u]['hits'][p] + (h2_map[u]['hits'][p] if u in h2_map else 0)
                for p in range(1, 6)}
        t = h1_total + h2_total
        combined.append({'username': u, 'user_id': row['user'].id,
                         'h1': h1_total, 'h2': h2_total, 'total': t, 'hits': hits})
    combined.sort(key=lambda x: (-x['total'], x['username']))
    for i, r in enumerate(combined):
        r['rank'] = i + 1
    return combined


def _render(mode, tab, half, live_race):
    live_positions = fetch_live_positions(live_race) if live_race else None

    if mode == 'normal':
        data = {
            'combined': _build(live_race=live_race, live_positions=live_positions),
            'races':    _build(race_type='race', live_race=live_race, live_positions=live_positions),
            'sprints':  _build(race_type='sprint', live_race=live_race, live_positions=live_positions),
        }
        return dict(mode='normal', tab=tab, half='overall', data=data, live_race=live_race)
    else:
        h1 = {'combined': _build(1, live_race=live_race, live_positions=live_positions),
              'races':    _build(1, 'race', live_race=live_race, live_positions=live_positions),
              'sprints':  _build(1, 'sprint', live_race=live_race, live_positions=live_positions)}
        h2 = {'combined': _build(2, live_race=live_race, live_positions=live_positions),
              'races':    _build(2, 'race', live_race=live_race, live_positions=live_positions),
              'sprints':  _build(2, 'sprint', live_race=live_race, live_positions=live_positions)}
        overall = _tapia_overall(h1['combined'], h2['combined'])
        show_overall = Setting.get('show_overall') == 'true'
        if not show_overall and half == 'overall':
            half = 'h1'
        return dict(mode='tapia', tab=tab, half=half, h1=h1, h2=h2,
                    overall=overall, show_overall=show_overall, live_race=live_race)


@leaderboard_bp.route('/')
def index():
    mode = request.args.get('mode', 'tapia')
    tab  = request.args.get('tab',  'combined')
    half = request.args.get('half', _current_half())
    history_races, history_lider = leaderPerWeek()
    ctx = _render(mode, tab, half, _live_race())
    ctx['history_races'] = history_races
    ctx['history_lider'] = history_lider
    return render_template('leaderboard/index.html', **ctx)


@leaderboard_bp.route('/live-refresh')
def live_refresh():
    mode = request.args.get('mode', 'tapia')
    tab  = request.args.get('tab',  'combined')
    half = request.args.get('half', _current_half())
    ctx = _render(mode, tab, half, _live_race())
    return render_template('leaderboard/_table.html', **ctx)


def leaderPerWeek():
    races = Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True).order_by(Race.race_date).all()
    users = User.query.order_by(User.username).all()
    
    predictions = Prediction.query.join(Race).filter(Race.season == Config.CURRENT_SEASON, Race.is_completed == True).all()
    results = RaceResult.query.join(Race).filter(Race.season == Config.CURRENT_SEASON, Race.is_completed == True).all()
    
    pred_map = {(p.race_id, p.user_id): p for p in predictions}
    res_map = {r.race_id: r for r in results}
    
    anotador = {user.id: 0 for user in users}
    lider = {}

    if not users:
        return races, lider

    for race in races:
        result = res_map.get(race.id)
        if not result:
            # If no result, leader remains the same as previous race, but let's re-assign anyway
            lider_id = max(anotador, key=anotador.get) if anotador else None
            if lider_id:
                lider[race.id] = next((u for u in users if u.id == lider_id), None)
            continue
            
        rlist = [result.pos1, result.pos2, result.pos3, result.pos4, result.pos5]
        
        for user in users:
            pred = pred_map.get((race.id, user.id))
            if pred:
                plist = [pred.pos1, pred.pos2, pred.pos3, pred.pos4, pred.pos5]
                for i in range(race.max_positions):
                    if plist[i] and rlist[i] and plist[i].lower() == rlist[i].lower():
                        anotador[user.id] += race.position_points[i]
                        
        if anotador:
            lider_id = max(anotador, key=anotador.get)
            lider[race.id] = next((u for u in users if u.id == lider_id), None)

    return races, lider
