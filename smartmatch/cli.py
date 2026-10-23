"""Flask CLI commands: smartmatch-seed, smartmatch-train, smartmatch-scan."""
import json

import click

from .alerts import send_stale_alerts
from .freshness import STALE_AFTER_DAYS, scan_stale_listings
from .recommender import RecommendationError, train_model
from .synthetic import generate


def register_cli(app):
    @app.cli.command("smartmatch-seed")
    @click.option("--donors", default=150, show_default=True)
    @click.option("--ngos", default=30, show_default=True)
    @click.option("--donations", default=1200, show_default=True, help="Target number of gifts over 18 months.")
    @click.option("--seed", default=42, show_default=True)
    def smartmatch_seed(donors, ngos, donations, seed):
        """Add synthetic Pune donors, NGOs and donation history (run after seed-db)."""
        generate(n_donors=donors, n_ngos=ngos, n_donations=donations, seed=seed, log=click.echo)

    @app.cli.command("smartmatch-train")
    @click.option("--holdout", default=0.2, show_default=True, help="Share of the most recent gifts held out.")
    @click.option("--seed", default=42, show_default=True)
    def smartmatch_train(holdout, seed):
        """Train the recommendation ranker on donation history and report holdout metrics."""
        try:
            bundle = train_model(holdout_frac=holdout, seed=seed, log=click.echo)
        except RecommendationError as e:
            raise click.ClickException(str(e))
        click.echo("Coefficients: " + json.dumps(bundle["coefficients"]))
        click.echo("Holdout metrics: " + json.dumps(bundle["metrics"], indent=2))

    @app.cli.command("smartmatch-scan")
    @click.option("--days", default=STALE_AFTER_DAYS, show_default=True)
    @click.option("--alert", is_flag=True, help="Also send nudges to NGOs with stale listings.")
    def smartmatch_scan(days, alert):
        """Flag listings not updated in N days as stale (run daily from cron)."""
        click.echo("Freshness scan: " + json.dumps(scan_stale_listings(days=days)))
        if alert:
            click.echo("Alerts: " + json.dumps(send_stale_alerts()))
