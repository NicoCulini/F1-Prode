from datetime import datetime
# pyrefly: ignore [missing-import]
from flask import Blueprint, render_template, request
from app.models import User, Race, Prediction, RaceResult, Setting
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


def _rank_key(total, tiebreak, username):
    """Standings order: points, then the player's season-wide race P1..P5 counts, then
    season-wide sprint P1..P3 counts (each higher-is-better), then username so the
    order is always stable. `tiebreak` is (race_hits, sprint_hits) over ALL completed
    races, regardless of which half or tab is being displayed."""
    race_hits, sprint_hits = tiebreak
    return (-total,
            *(-race_hits[p] for p in range(1, 6)),
            *(-sprint_hits[p] for p in range(1, 4)),
            username)


def _empty_hits():
    return {p: 0 for p in range(1, 6)}


def _count_hits(pred, result, race, race_hits, sprint_hits):
    """Add this prediction's exact-position hits into race_hits or sprint_hits; return points."""
    plist = [pred.pos1, pred.pos2, pred.pos3, pred.pos4, pred.pos5]
    rlist = [result.pos1, result.pos2, result.pos3, result.pos4, result.pos5]
    target = sprint_hits if race.race_type == 'sprint' else race_hits
    points = 0
    for i in range(race.max_positions):
        if plist[i] and rlist[i] and plist[i].lower() == rlist[i].lower():
            target[i + 1] += 1
            points += race.position_points[i]
    return points


def _global_tiebreak():
    """{user_id: (race_hits, sprint_hits)} over every completed race of the season."""
    races = Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True).all()
    race_ids = [r.id for r in races]
    preds = Prediction.query.filter(Prediction.race_id.in_(race_ids)).all()
    results = {r.race_id: r for r in RaceResult.query.filter(RaceResult.race_id.in_(race_ids)).all()}
    race_by_id = {r.id: r for r in races}
    stats = {u.id: (_empty_hits(), _empty_hits()) for u in User.query.all()}
    for pred in preds:
        result = results.get(pred.race_id)
        if result and pred.user_id in stats:
            _count_hits(pred, result, race_by_id[pred.race_id], *stats[pred.user_id])
    return stats


def _build(season_half=None, race_type=None, tiebreak=None):
    """Return sorted rows with per-position hit counts and total points
    over the completed races matching the filters. Ties are broken with season-wide
    stats (see _rank_key), not just the races in this scope."""
    if tiebreak is None:
        tiebreak = _global_tiebreak()
    q = Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True)
    if season_half is not None:
        q = q.filter_by(season_half=season_half)
    if race_type is not None:
        q = q.filter_by(race_type=race_type)
    races = q.order_by(Race.race_date).all()

    users = User.query.order_by(User.username).all()

    # Load everything once instead of two queries per user per race.
    race_ids = [r.id for r in races]
    pred_map = {(p.user_id, p.race_id): p
                for p in Prediction.query.filter(Prediction.race_id.in_(race_ids)).all()}
    result_map = {r.race_id: r
                  for r in RaceResult.query.filter(RaceResult.race_id.in_(race_ids)).all()}

    rows = []
    for user in users:
        total        = 0
        hits         = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        race_hits    = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        sprint_hits  = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for race in races:
            pred   = pred_map.get((user.id, race.id))
            result = result_map.get(race.id)
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

        rows.append({'user': user, 'total': total, 'hits': hits,
                     'race_hits': race_hits, 'sprint_hits': sprint_hits,
                     'total_hits': sum(hits.values())})

    rows.sort(key=lambda x: _rank_key(x['total'], tiebreak[x['user'].id], x['user'].username))
    for i, row in enumerate(rows):
        row['rank'] = i + 1
    return rows


def _tapia_overall(h1_rows, h2_rows, tiebreak):
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
    combined.sort(key=lambda x: _rank_key(x['total'], tiebreak[x['user_id']], x['username']))
    for i, r in enumerate(combined):
        r['rank'] = i + 1
    return combined


def _render(mode, tab, half):
    tb = _global_tiebreak()   # computed once, shared by every table below
    if mode == 'normal':
        data = {
            'combined': _build(tiebreak=tb),
            'races':    _build(race_type='race', tiebreak=tb),
            'sprints':  _build(race_type='sprint', tiebreak=tb),
        }
        return dict(mode='normal', tab=tab, half='overall', data=data)
    else:
        h1 = {'combined': _build(1, tiebreak=tb),
              'races':    _build(1, 'race', tiebreak=tb),
              'sprints':  _build(1, 'sprint', tiebreak=tb)}
        h2 = {'combined': _build(2, tiebreak=tb),
              'races':    _build(2, 'race', tiebreak=tb),
              'sprints':  _build(2, 'sprint', tiebreak=tb)}
        overall = _tapia_overall(h1['combined'], h2['combined'], tb)
        show_overall = Setting.get('show_overall') == 'true'
        if not show_overall and half == 'overall':
            half = 'h1'
        return dict(mode='tapia', tab=tab, half=half, h1=h1, h2=h2,
                    overall=overall, show_overall=show_overall)


@leaderboard_bp.route('/')
def index():
    mode = request.args.get('mode', 'tapia')
    tab  = request.args.get('tab',  'combined')
    half = request.args.get('half', _current_half())
    ctx = _render(mode, tab, half)
    # In Tapia mode the half tabs scope the history; 'overall' (and normal mode) show the whole season.
    season_half = {'h1': 1, 'h2': 2}.get(ctx['half']) if mode == 'tapia' else None
    history_races, history_lider = leaderPerWeek(season_half)
    ctx['history_races'] = history_races
    ctx['history_lider'] = history_lider
    return render_template('leaderboard/index.html', **ctx)


def leaderPerWeek(season_half=None):
    """Leader after each completed race. With season_half (1 or 2) only that half's
    races are listed and the running points restart at the half. Ties are broken by
    the player's season-to-date race/sprint hit counts across ALL races so far."""
    all_races = (Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True)
                 .order_by(Race.race_date).all())
    races = [r for r in all_races if season_half is None or r.season_half == season_half]
    users = User.query.order_by(User.username).all()
    lider = {}
    if not users:
        return races, lider

    race_ids = [r.id for r in all_races]
    pred_map = {(p.race_id, p.user_id): p
                for p in Prediction.query.filter(Prediction.race_id.in_(race_ids)).all()}
    res_map = {r.race_id: r for r in RaceResult.query.filter(RaceResult.race_id.in_(race_ids)).all()}

    points = {u.id: 0 for u in users}                                     # scope points
    tiebreak = {u.id: (_empty_hits(), _empty_hits()) for u in users}      # season-to-date hits
    in_scope = {r.id for r in races}

    for race in all_races:
        result = res_map.get(race.id)
        if result:
            for user in users:
                pred = pred_map.get((race.id, user.id))
                if pred:
                    pts = _count_hits(pred, result, race, *tiebreak[user.id])
                    if race.id in in_scope:
                        points[user.id] += pts
        if race.id in in_scope:
            lider[race.id] = min(users, key=lambda u: _rank_key(points[u.id], tiebreak[u.id], u.username))

    return races, lider

    def current_leader():
        return min(users, key=lambda u: _rank_key(stats[u.id]['total'], stats[u.id]['race_hits'],
                                                  stats[u.id]['sprint_hits'], u.username))

    for race in races:
        result = res_map.get(race.id)
        if result:
            rlist = [result.pos1, result.pos2, result.pos3, result.pos4, result.pos5]
            for user in users:
                pred = pred_map.get((race.id, user.id))
                if not pred:
                    continue
                plist = [pred.pos1, pred.pos2, pred.pos3, pred.pos4, pred.pos5]
                st = stats[user.id]
                for i in range(race.max_positions):
                    if plist[i] and rlist[i] and plist[i].lower() == rlist[i].lower():
                        st['total'] += race.position_points[i]
                        st['sprint_hits' if race.race_type == 'sprint' else 'race_hits'][i + 1] += 1
        lider[race.id] = current_leader()

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
