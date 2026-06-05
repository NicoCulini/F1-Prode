from datetime import datetime
from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import check_password_hash


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    predictions = db.relationship('Prediction', backref='user', lazy=True)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Race(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)
    season = db.Column(db.Integer, nullable=False, default=2025)
    race_date = db.Column(db.DateTime)
    race_type = db.Column(db.String(10), nullable=False, default='race')  # 'race' or 'sprint'
    season_half = db.Column(db.Integer, default=1)  # 1 = before summer break, 2 = after
    is_completed = db.Column(db.Boolean, default=False)
    predictions_open = db.Column(db.Boolean, default=True)
    predictions = db.relationship('Prediction', backref='race', lazy=True)
    result = db.relationship('RaceResult', backref='race', uselist=False)

    @property
    def max_positions(self):
        return 3 if self.race_type == 'sprint' else 5

    @property
    def position_points(self):
        return [3, 2, 1] if self.race_type == 'sprint' else [5, 4, 3, 2, 1]

    def __repr__(self):
        return f'<Race {self.season} R{self.round_number} {self.name} [{self.race_type}]>'


class Driver(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(3))
    family_name = db.Column(db.String(100), nullable=False)
    given_name = db.Column(db.String(100))
    team = db.Column(db.String(100))
    season = db.Column(db.Integer, nullable=False)

    @property
    def display_name(self):
        return self.family_name

    def __repr__(self):
        return f'<Driver {self.code} {self.family_name}>'


class Prediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    race_id = db.Column(db.Integer, db.ForeignKey('race.id'), nullable=False)
    pos1 = db.Column(db.String(100))
    pos2 = db.Column(db.String(100))
    pos3 = db.Column(db.String(100))
    pos4 = db.Column(db.String(100))
    pos5 = db.Column(db.String(100))
    score = db.Column(db.Integer, default=0)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'race_id', name='uq_user_race'),)

    def positions(self):
        race = Race.query.get(self.race_id)
        if race.race_type == 'sprint':
            return [self.pos1, self.pos2, self.pos3]
        return [self.pos1, self.pos2, self.pos3, self.pos4, self.pos5]

    def calculate_score(self):
        race = Race.query.get(self.race_id)
        result = RaceResult.query.filter_by(race_id=self.race_id).first()
        if not result:
            return 0
        points = race.position_points
        pred = [self.pos1, self.pos2, self.pos3, self.pos4, self.pos5]
        res = [result.pos1, result.pos2, result.pos3, result.pos4, result.pos5]
        total = 0
        for i in range(race.max_positions):
            if pred[i] and res[i] and pred[i].lower() == res[i].lower():
                total += points[i]
        return total


class RaceResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    race_id = db.Column(db.Integer, db.ForeignKey('race.id'), nullable=False, unique=True)
    pos1 = db.Column(db.String(100))
    pos2 = db.Column(db.String(100))
    pos3 = db.Column(db.String(100))
    pos4 = db.Column(db.String(100))
    pos5 = db.Column(db.String(100))
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    def as_list(self):
        return [self.pos1, self.pos2, self.pos3, self.pos4, self.pos5]
