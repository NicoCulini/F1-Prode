import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'f1-prode-dev-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///f1prode.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CURRENT_SEASON = 2026


def generate_secret_key():
    """Run once to print a secure random key: python -c "from config import generate_secret_key; generate_secret_key()" """
    import secrets
    print(secrets.token_hex(32))
