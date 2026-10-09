"""Resolve which picture a user has: uploaded > bundled static/img/users/<name>.png > default."""
import os
from flask import current_app, url_for

UPLOAD_EXTS = ('png', 'jpg', 'webp')


def avatar_dir():
    return os.path.join(current_app.instance_path, 'avatars')


def uploaded_avatar_path(user_id):
    for ext in UPLOAD_EXTS:
        path = os.path.join(avatar_dir(), f'{user_id}.{ext}')
        if os.path.exists(path):
            return path
    return None


def legacy_avatar_filename(username):
    """Path relative to the static folder, if a bundled picture exists for this username."""
    rel = f'img/users/{username.lower()}.png'
    return rel if os.path.exists(os.path.join(current_app.static_folder, rel)) else None


def avatar_url(user):
    """URL for the user's picture. Includes a version (file mtime) so browsers can cache it
    for a long time and still pick up a new upload immediately. Users without a custom
    picture all share the one static default, so it is downloaded once."""
    path = uploaded_avatar_path(user.id)
    if path:
        return url_for('auth.avatar', user_id=user.id, v=int(os.path.getmtime(path)))
    legacy = legacy_avatar_filename(user.username)
    if legacy:
        v = int(os.path.getmtime(os.path.join(current_app.static_folder, legacy)))
        return url_for('static', filename=legacy, v=v)
    return static_url('img/default-avatar.jpg')


def static_url(filename):
    """url_for('static', ...) with a version query so long cache lifetimes are safe."""
    path = os.path.join(current_app.static_folder, filename)
    v = int(os.path.getmtime(path)) if os.path.exists(path) else 0
    return url_for('static', filename=filename, v=v)
