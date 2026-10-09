import os
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file, abort
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app import db
from app.models import User
from app.avatars import avatar_dir as _avatar_dir, uploaded_avatar_path, legacy_avatar_filename

auth_bp = Blueprint('auth', __name__)

RESET_MAX_AGE = 3600  # seconds
AVATAR_MAX_BYTES = 2 * 1024 * 1024


def _sniff_image_ext(data):
    """Return 'png'/'jpg'/'webp' from magic bytes, else None (SVG etc. are rejected on purpose)."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


def _remove_avatar_files(user_id):
    for ext in ('png', 'jpg', 'webp'):
        path = os.path.join(_avatar_dir(), f'{user_id}.{ext}')
        if os.path.exists(path):
            os.remove(path)


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='password-reset')


def make_reset_token(user):
    # Embeds a slice of the current hash, so the link dies once the password changes.
    return _reset_serializer().dumps({'id': user.id, 'h': user.password_hash[-12:]})


def user_from_reset_token(token):
    try:
        data = _reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    user = User.query.get(data.get('id'))
    if user and user.password_hash[-12:] == data.get('h'):
        return user
    return None


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


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = user_from_reset_token(token)
    if not user:
        flash('This reset link is invalid or has expired. Ask an admin for a new one.', 'danger')
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        new_pw  = request.form.get('new_password', '')
        confirm = request.form.get('confirm', '')
        if len(new_pw) < 6:
            flash('New password must be at least 6 characters.', 'danger')
        elif new_pw != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            user.password_hash = generate_password_hash(new_pw)
            db.session.commit()
            flash('Password updated. You can sign in now.', 'success')
            return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', username=user.username)


@auth_bp.route('/avatar/<int:user_id>')
def avatar(user_id):
    """Serve an uploaded picture. URLs carry ?v=<mtime> (see app.avatars.avatar_url), so the
    response can be cached for a year; a new upload changes the URL."""
    path = uploaded_avatar_path(user_id)
    if path:
        return send_file(path, max_age=31536000)
    user = User.query.get(user_id)
    if not user:
        abort(404)
    legacy = legacy_avatar_filename(user.username)
    fallback = (os.path.join(current_app.static_folder, legacy) if legacy
                else os.path.join(current_app.static_folder, 'img', 'default-avatar.jpg'))
    return send_file(fallback, max_age=3600)


@auth_bp.route('/change-avatar', methods=['POST'])
@login_required
def change_avatar():
    if request.form.get('remove'):
        _remove_avatar_files(current_user.id)
        flash('Profile picture removed.', 'success')
        return redirect(url_for('auth.change_password'))
    file = request.files.get('avatar')
    data = file.read(AVATAR_MAX_BYTES + 1) if file else b''
    if not data:
        flash('Choose an image first.', 'danger')
    elif len(data) > AVATAR_MAX_BYTES:
        flash('Image is too large (max 2 MB).', 'danger')
    else:
        ext = _sniff_image_ext(data)
        if not ext:
            flash('Use a PNG, JPG or WebP image.', 'danger')
        else:
            os.makedirs(_avatar_dir(), exist_ok=True)
            _remove_avatar_files(current_user.id)
            with open(os.path.join(_avatar_dir(), f'{current_user.id}.{ext}'), 'wb') as f:
                f.write(data)
            flash('Profile picture updated.', 'success')
    return redirect(url_for('auth.change_password'))


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))
