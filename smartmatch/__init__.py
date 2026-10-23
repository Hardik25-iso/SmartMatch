"""SmartMatch matching engine: ML donor-to-NGO recommendations and need-freshness tracking.

app.py owns the Flask app, the SQLAlchemy instance and every model. It hands them to
init_smartmatch() instead of this package importing from app, which avoids a circular
import and a second copy of the models when the app is started with `python app.py`.
"""
from . import ctx


def init_smartmatch(app, db, models):
    ctx.app = app
    ctx.db = db
    ctx.models = models

    from .api import api_bp
    from .views import smartmatch_bp
    from .cli import register_cli

    app.register_blueprint(smartmatch_bp)
    app.register_blueprint(api_bp)
    register_cli(app)
