from datetime import datetime
from flask import Blueprint, render_template, request
from app.models import Race
from config import Config

calendar_bp = Blueprint('calendar', __name__)


@calendar_bp.route('/')
def index():
    view = request.args.get('view', 'tapia')
    races = (Race.query
             .filter_by(season=Config.CURRENT_SEASON)
             .order_by(Race.race_date)
             .all())
    now = datetime.utcnow()
    return render_template('calendar/index.html', races=races, now=now, view=view)
