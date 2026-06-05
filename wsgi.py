import sys
import os

# ── Update this path to match your PythonAnywhere username ──────────────────
# e.g. if your username is "nico": '/home/nico/F1 Prode'
project_home = '/home/YOUR_USERNAME/F1 Prode'

if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import create_app
application = create_app()
