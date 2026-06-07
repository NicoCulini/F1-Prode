from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models import Race, Prediction, Driver, RaceResult
from app.scraper import fetch_qualifying_top10
from config import Config

predictions_bp = Blueprint('predictions', __name__)


def _is_locked(race):
    """Predictions lock 4 hours before race start."""
    if not race.race_date:
        return False
    return datetime.utcnow() >= race.race_date - timedelta(hours=4)


def _visible_races():
    """Completed races + the single next upcoming race (always shown)."""
    completed = (Race.query
                 .filter_by(season=Config.CURRENT_SEASON, is_completed=True)
                 .order_by(Race.race_date.desc())
                 .all())
    next_race = (Race.query
                 .filter_by(season=Config.CURRENT_SEASON, is_completed=False)
                 .order_by(Race.race_date)
                 .first())
    return ([next_race] if next_race else []) + completed


@predictions_bp.route('/')
@login_required
def index():
    races = _visible_races()
    user_preds = {p.race_id: p for p in Prediction.query.filter_by(user_id=current_user.id).all()}
    results = {r.race_id: r for r in RaceResult.query.all()}
    return render_template('predictions/index.html',
                           past_races=races,
                           user_preds=user_preds,
                           results=results,
                           my_preds=None)


@predictions_bp.route('/user/<int:user_id>')
@login_required
def user_predictions(user_id):
    from app.models import User
    viewed_user = User.query.get_or_404(user_id)
    races = _visible_races()
    user_preds = {p.race_id: p for p in Prediction.query.filter_by(user_id=user_id).all()}
    results = {r.race_id: r for r in RaceResult.query.all()}
    # Race IDs where the *viewing* user has submitted — gates upcoming race picks
    my_preds = {p.race_id for p in Prediction.query.filter_by(user_id=current_user.id).all()}
    return render_template('predictions/index.html',
                           past_races=races,
                           user_preds=user_preds,
                           results=results,
                           viewed_user=viewed_user,
                           my_preds=my_preds)


@predictions_bp.route('/race/<int:race_id>', methods=['GET', 'POST'])
@login_required
def predict(race_id):
    race = Race.query.get_or_404(race_id)

    if not race.predictions_open:
        flash('Predictions are closed for this race.', 'warning')
        return redirect(url_for('main.index'))

    if _is_locked(race):
        flash('Predictions are locked — less than 4 hours to race start.', 'warning')
        return redirect(url_for('main.index'))

    # Both races and sprints auto-unlock once qualifying/shootout data appears in OpenF1
    qualifying = fetch_qualifying_top10(race)
    if not qualifying:
        flash('Qualifying hasn\'t happened yet — check back after qualifying.', 'warning')
        return redirect(url_for('main.index'))

    drivers = (Driver.query
               .filter_by(season=Config.CURRENT_SEASON)
               .order_by(Driver.family_name)
               .all())

    existing = Prediction.query.filter_by(user_id=current_user.id, race_id=race_id).first()

    if request.method == 'POST':
        positions = []
        for i in range(1, race.max_positions + 1):
            positions.append(request.form.get(f'pos{i}', '').strip() or None)

        if len(set(p for p in positions if p)) < len([p for p in positions if p]):
            flash('Each driver can only be picked once.', 'danger')
        elif not all(positions):
            flash('Please fill in all positions.', 'danger')
        else:
            if existing:
                existing.pos1, existing.pos2, existing.pos3 = positions[0], positions[1], positions[2]
                existing.pos4 = positions[3] if len(positions) > 3 else None
                existing.pos5 = positions[4] if len(positions) > 4 else None
                existing.submitted_at = datetime.utcnow()
            else:
                pred = Prediction(
                    user_id=current_user.id,
                    race_id=race_id,
                    pos1=positions[0], pos2=positions[1], pos3=positions[2],
                    pos4=positions[3] if len(positions) > 3 else None,
                    pos5=positions[4] if len(positions) > 4 else None,
                )
                db.session.add(pred)
            db.session.commit()
            flash('Prediction saved!', 'success')
            return redirect(url_for('main.index'))

    return render_template('predictions/predict.html',
                           race=race, drivers=drivers,
                           existing=existing, qualifying=qualifying)
