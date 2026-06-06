from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from app import db
from app.models import Race, Driver, RaceResult, Prediction, User, Setting
from app.scraper import fetch_season_schedule, fetch_season_drivers, fetch_race_result
from config import Config

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@admin_required
def dashboard():
    stats = {
        'users': User.query.count(),
        'races': Race.query.filter_by(season=Config.CURRENT_SEASON).count(),
        'completed': Race.query.filter_by(season=Config.CURRENT_SEASON, is_completed=True).count(),
        'drivers': Driver.query.filter_by(season=Config.CURRENT_SEASON).count(),
    }
    show_overall = Setting.get('show_overall') == 'true'
    users = User.query.order_by(User.username).all()
    return render_template('admin/dashboard.html', stats=stats, show_overall=show_overall, users=users)


@admin_bp.route('/toggle-overall')
@admin_required
def toggle_overall():
    current = Setting.get('show_overall')
    new_val = 'false' if current == 'true' else 'true'
    Setting.set('show_overall', new_val)
    db.session.commit()
    state = 'visible' if new_val == 'true' else 'hidden'
    flash(f'Overall Tapia standings are now {state}.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/races')
@admin_required
def races():
    all_races = (Race.query
                 .filter_by(season=Config.CURRENT_SEASON)
                 .order_by(Race.race_date)
                 .all())
    return render_template('admin/races.html', races=all_races, season=Config.CURRENT_SEASON)


@admin_bp.route('/races/import-schedule')
@admin_required
def import_schedule():
    ok, msg = fetch_season_schedule(Config.CURRENT_SEASON)
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/import-drivers')
@admin_required
def import_drivers():
    ok, msg = fetch_season_drivers(Config.CURRENT_SEASON)
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/<int:race_id>/fetch-result')
@admin_required
def fetch_result(race_id):
    race = Race.query.get_or_404(race_id)
    ok, msg = fetch_race_result(race)
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/<int:race_id>/add-sprint')
@admin_required
def add_sprint(race_id):
    race = Race.query.get_or_404(race_id)
    existing = Race.query.filter_by(season=race.season, round_number=race.round_number, race_type='sprint').first()
    if existing:
        flash(f'A sprint already exists for round {race.round_number}.', 'warning')
    else:
        sprint = Race(
            name=race.name.replace('Grand Prix', 'Sprint').replace('Grand Prix', 'Sprint'),
            round_number=race.round_number,
            season=race.season,
            race_date=race.race_date,
            race_type='sprint',
            season_half=race.season_half,
            is_completed=False,
            predictions_open=True,
        )
        db.session.add(sprint)
        db.session.commit()
        flash(f'Sprint added for {race.name}.', 'success')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/<int:race_id>/toggle-predictions')
@admin_required
def toggle_predictions(race_id):
    race = Race.query.get_or_404(race_id)
    race.predictions_open = not race.predictions_open
    db.session.commit()
    state = 'opened' if race.predictions_open else 'closed'
    flash(f'Predictions {state} for {race.name}.', 'success')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/<int:race_id>/set-half', methods=['POST'])
@admin_required
def set_half(race_id):
    race = Race.query.get_or_404(race_id)
    half = request.form.get('half', '1')
    race.season_half = int(half)
    db.session.commit()
    flash(f'{race.name} assigned to Half {half}.', 'success')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/add', methods=['POST'])
@admin_required
def add_race():
    name = request.form.get('name', '').strip()
    round_num = request.form.get('round_number', type=int)
    race_type = request.form.get('race_type', 'race')
    season_half = request.form.get('season_half', 1, type=int)
    date_str = request.form.get('race_date', '')

    if not name or not round_num:
        flash('Name and round number are required.', 'danger')
        return redirect(url_for('admin.races'))

    from datetime import datetime
    race_date = None
    if date_str:
        try:
            race_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            pass

    race = Race(
        name=name,
        round_number=round_num,
        season=Config.CURRENT_SEASON,
        race_date=race_date,
        race_type=race_type,
        season_half=season_half,
    )
    db.session.add(race)
    db.session.commit()
    flash(f'{name} added.', 'success')
    return redirect(url_for('admin.races'))


@admin_bp.route('/races/<int:race_id>/set-result', methods=['POST'])
@admin_required
def set_result(race_id):
    race = Race.query.get_or_404(race_id)
    positions = []
    for i in range(1, race.max_positions + 1):
        v = request.form.get(f'pos{i}', '').strip() or None
        positions.append(v)
    while len(positions) < 5:
        positions.append(None)

    existing = RaceResult.query.filter_by(race_id=race_id).first()
    if existing:
        existing.pos1, existing.pos2, existing.pos3 = positions[0], positions[1], positions[2]
        existing.pos4, existing.pos5 = positions[3], positions[4]
    else:
        result = RaceResult(
            race_id=race_id,
            pos1=positions[0], pos2=positions[1], pos3=positions[2],
            pos4=positions[3], pos5=positions[4],
        )
        db.session.add(result)

    race.is_completed = True
    race.predictions_open = False
    db.session.flush()

    for pred in Prediction.query.filter_by(race_id=race_id).all():
        pred.score = pred.calculate_score()

    db.session.commit()
    flash(f'Result saved for {race.name}.', 'success')
    return redirect(url_for('admin.races'))


@admin_bp.route('/users')
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=all_users)


@admin_bp.route('/users/<int:user_id>/toggle-admin')
@admin_required
def toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't remove your own admin status.", 'warning')
    else:
        user.is_admin = not user.is_admin
        db.session.commit()
        flash(f"Admin status {'granted to' if user.is_admin else 'removed from'} {user.username}.", 'success')
    return redirect(url_for('admin.users'))
