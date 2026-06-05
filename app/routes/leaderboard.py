from datetime import datetime
from flask import Blueprint, render_template, request
from app.models import User, Race, Prediction, RaceResult
from config import Config


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

leaderboard_bp = Blueprint('leaderboard', __name__)


def _build(season_half=None, race_type=None):
    """Return sorted rows with per-position hit counts and total points."""
    q = Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True)
    if season_half is not None:
        q = q.filter_by(season_half=season_half)
    if race_type is not None:
        q = q.filter_by(race_type=race_type)
    races = q.order_by(Race.race_date).all()

    users = User.query.order_by(User.username).all()
    rows = []
    for user in users:
        total = 0
        hits = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
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
        rows.append({'user': user, 'total': total, 'hits': hits,
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


@leaderboard_bp.route('/')
def index():
    mode = request.args.get('mode', 'tapia')
    tab  = request.args.get('tab',  'combined')
    half = request.args.get('half', _current_half())

    if mode == 'normal':
        data = {
            'combined': _build(),
            'races':    _build(race_type='race'),
            'sprints':  _build(race_type='sprint'),
        }
        return render_template('leaderboard/index.html',
                               mode='normal', tab=tab, half='overall', data=data)
    else:
        h1 = {'combined': _build(1), 'races': _build(1, 'race'), 'sprints': _build(1, 'sprint')}
        h2 = {'combined': _build(2), 'races': _build(2, 'race'), 'sprints': _build(2, 'sprint')}
        overall = _tapia_overall(h1['combined'], h2['combined'])
        return render_template('leaderboard/index.html',
                               mode='tapia', tab=tab, half=half,
                               h1=h1, h2=h2, overall=overall)
