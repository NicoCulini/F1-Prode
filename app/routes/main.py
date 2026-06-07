from datetime import datetime, timedelta
from flask import Blueprint, render_template
from app.models import Race
from config import Config

main_bp = Blueprint('main', __name__)


def get_next_race():
    """Next upcoming race by date — no API call, purely DB-based."""
    now = datetime.utcnow()
    return (Race.query
            .filter_by(season=Config.CURRENT_SEASON, is_completed=False, predictions_open=True)
            .filter(Race.race_date >= now - timedelta(hours=4))  # still show up to 4h before race
            .order_by(Race.race_date)
            .first())


@main_bp.route('/')
def index():
    from flask_login import current_user
    from app.models import Prediction

    next_race = get_next_race()
    recent = (Race.query
              .filter_by(season=Config.CURRENT_SEASON, is_completed=True)
              .order_by(Race.race_date.desc())
              .limit(4)
              .all())

    user_preds = {}
    already_predicted = False
    if current_user.is_authenticated:
        ids = [r.id for r in recent]
        user_preds = {p.race_id: p for p in
                      Prediction.query.filter(Prediction.user_id == current_user.id,
                                              Prediction.race_id.in_(ids)).all()}
        if next_race:
            already_predicted = Prediction.query.filter_by(
                user_id=current_user.id, race_id=next_race.id).first() is not None

    return render_template('index.html', next_race=next_race, recent=recent,
                           user_preds=user_preds, already_predicted=already_predicted)
