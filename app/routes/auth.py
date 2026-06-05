from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from app import db
from app.models import User

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        flash('Invalid username or password.', 'danger')
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not username or not password:
            flash('Username and password are required.', 'danger')
        elif len(username) < 3:
            flash('Username must be at least 3 characters.', 'danger')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        elif User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
        else:
            user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            flash(f'Welcome, {username}!', 'success')
            return redirect(url_for('main.index'))
    return render_template('auth/register.html')


@auth_bp.route('/change-username', methods=['POST'])
@login_required
def change_username():
    new_name = request.form.get('username', '').strip()
    if len(new_name) < 3:
        flash('Username must be at least 3 characters.', 'danger')
    elif User.query.filter(User.username == new_name, User.id != current_user.id).first():
        flash('That username is already taken.', 'danger')
    else:
        current_user.username = new_name
        db.session.commit()
        flash('Username updated.', 'success')
    return redirect(url_for('auth.change_password'))


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current', '')
        new_pw  = request.form.get('new_password', '')
        confirm = request.form.get('confirm', '')
        if not current_user.check_password(current):
            flash('Current password is incorrect.', 'danger')
        elif len(new_pw) < 6:
            flash('New password must be at least 6 characters.', 'danger')
        elif new_pw != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            current_user.password_hash = generate_password_hash(new_pw)
            db.session.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('main.index'))
    return render_template('auth/change_password.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))
