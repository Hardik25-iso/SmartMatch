"""Email and WhatsApp nudges asking NGOs to confirm or update stale listings.

Safe by default: unless ALERTS_DRY_RUN=false *and* a channel's credentials are set,
messages are only logged and recorded in AlertLog with status 'dry_run'.

Demo and synthetic NGOs (contact emails on example.com or *.test) have made-up phone
numbers that could belong to real people, so they are never messaged for real. To demo
live delivery, set ALERTS_REDIRECT_EMAIL / ALERTS_REDIRECT_WHATSAPP to your own contact:
every live message then goes there instead.
"""
import os
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import ctx
from .freshness import get_stale_listings


def _env_flag(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def alerts_dry_run():
    return _env_flag("ALERTS_DRY_RUN", True)


def is_synthetic_contact(orphanage):
    email = (orphanage.contact_email or "").lower()
    return email.endswith("@example.com") or email.endswith(".test")


class EmailNotifier:
    channel = "email"

    def __init__(self):
        self.host = os.environ.get("SMTP_HOST")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ.get("SMTP_USER")
        self.password = os.environ.get("SMTP_PASSWORD")
        self.sender = os.environ.get("SMTP_FROM") or self.user
        self.use_tls = _env_flag("SMTP_USE_TLS", True)
        self.redirect = os.environ.get("ALERTS_REDIRECT_EMAIL")

    @property
    def configured(self):
        return bool(self.host and self.sender)

    def recipient(self, orphanage):
        return orphanage.contact_email

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


class WhatsAppNotifier:
    channel = "whatsapp"

    def __init__(self):
        self.sid = os.environ.get("TWILIO_ACCOUNT_SID")
        self.token = os.environ.get("TWILIO_AUTH_TOKEN")
        self.sender = os.environ.get("TWILIO_WHATSAPP_FROM")  # e.g. whatsapp:+14155238886
        self.redirect = os.environ.get("ALERTS_REDIRECT_WHATSAPP")

    @property
    def configured(self):
        return bool(self.sid and self.token and self.sender)

    def recipient(self, orphanage):
        return orphanage.contact_phone

    def send(self, to, subject, body):
        from twilio.rest import Client

        sender = self.sender if self.sender.startswith("whatsapp:") else f"whatsapp:{self.sender}"
        Client(self.sid, self.token).messages.create(from_=sender, to=f"whatsapp:{to}", body=body)


def default_notifiers():
    return [EmailNotifier(), WhatsAppNotifier()]


def build_message(orphanage_name, listings, dashboard_url):
    lines = [f"Hello {orphanage_name},", "",
             f"{len(listings)} of your needs on SmartMatch haven't been updated in over 30 days:"]
    for item in listings[:10]:
        lines.append(f"  - {item['item_name']} x{item['quantity_needed']} ({item['age_days']} days)")
    if len(listings) > 10:
        lines.append(f"  ...and {len(listings) - 10} more")
    lines += ["", "Donors are matched to fresh needs first. Please confirm the items you still need, "
                  "or edit or remove the ones you don't:", dashboard_url, "", "Thank you, the SmartMatch team"]
    subject = f"SmartMatch: please confirm {len(listings)} open need{'s' if len(listings) != 1 else ''}"
    return subject, "\n".join(lines)


def send_stale_alerts(dry_run=None, cooldown_days=None, notifiers=None, now=None):
    """One message per NGO with stale listings, on every channel that has a recipient.

    Skips NGOs alerted within the cooldown. A failure on one channel or NGO is recorded
    and does not stop the rest. Returns a summary dict.
    """
    db, m = ctx.db, ctx.models
    now = now or datetime.utcnow()
    dry_run = alerts_dry_run() if dry_run is None else dry_run
    cooldown_days = int(os.environ.get("ALERT_COOLDOWN_DAYS", "7")) if cooldown_days is None else cooldown_days
    notifiers = default_notifiers() if notifiers is None else notifiers
    dashboard_url = os.environ.get("APP_BASE_URL", "http://localhost").rstrip("/") + "/dashboard"

    by_ngo = defaultdict(list)
    for listing in get_stale_listings(now=now):
        by_ngo[listing["orphanage_id"]].append(listing)

    summary = {"ngos_with_stale": len(by_ngo), "skipped_cooldown": 0, "sent": 0, "dry_run": 0,
               "failed": 0, "no_recipient": 0, "dry_run_mode": dry_run}
    cooldown_start = now - timedelta(days=cooldown_days)

    for orphanage_id, listings in by_ngo.items():
        recent = m.AlertLog.query.filter(
            m.AlertLog.orphanage_id == orphanage_id,
            m.AlertLog.created_at >= cooldown_start,
            # Rehearsals don't block the first real send after dry run is switched off.
            m.AlertLog.status.in_(["sent", "dry_run"] if dry_run else ["sent"]),
        ).first()
        if recent:
            summary["skipped_cooldown"] += 1
            continue

        orphanage = db.session.get(m.Orphanage, orphanage_id)
        subject, body = build_message(orphanage.name, listings, dashboard_url)
        for notifier in notifiers:
            to = notifier.recipient(orphanage)
            if not to:
                summary["no_recipient"] += 1
                continue
            log = m.AlertLog(orphanage_id=orphanage_id, channel=notifier.channel, recipient=to,
                             item_count=len(listings), message=body, created_at=now)
            redirect = getattr(notifier, "redirect", None)
            if dry_run or not notifier.configured or (is_synthetic_contact(orphanage) and not redirect):
                log.status = "dry_run"
                if not dry_run and notifier.configured:
                    log.error = "Not delivered: synthetic/demo contact. Set ALERTS_REDIRECT_* to demo live sends."
                ctx.app.logger.info("SmartMatch alert (dry run) %s -> %s: %s", notifier.channel, to, subject)
            else:
                if redirect:
                    log.recipient = f"{redirect} (for {to})"
                try:
                    notifier.send(redirect or to, subject, body)
                    log.status = "sent"
                except Exception as e:
                    log.status = "failed"
                    log.error = str(e)[:1000]
                    ctx.app.logger.warning("SmartMatch alert failed %s -> %s: %s", notifier.channel, to, e)
            summary[log.status] += 1
            db.session.add(log)
        db.session.commit()
    return summary


def recent_alerts(limit=20):
    m = ctx.models
    return m.AlertLog.query.order_by(m.AlertLog.created_at.desc(), m.AlertLog.id.desc()).limit(limit).all()
