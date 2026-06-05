import click
from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()


@app.cli.command('create-admin')
@click.argument('username')
@click.argument('password')
def create_admin(username, password):
    """Create an admin user: flask create-admin <username> <password>"""
    existing = User.query.filter_by(username=username).first()
    if existing:
        click.echo(f"User '{username}' already exists.")
        return
    user = User(username=username, password_hash=generate_password_hash(password), is_admin=True)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Admin '{username}' created.")


if __name__ == '__main__':
    app.run(debug=True)
