from datetime import timedelta

from app import AlertLog
from smartmatch.alerts import send_stale_alerts
from smartmatch.freshness import scan_stale_listings


class FakeNotifier:
    def __init__(self, channel, configured=True, fail=False, redirect=None):
        self.channel, self.configured, self.fail, self.sent = channel, configured, fail, []
        self.redirect = redirect

    def recipient(self, orphanage):
        return orphanage.contact_email if self.channel == "email" else orphanage.contact_phone

    def send(self, to, subject, body):
        if self.fail:
            raise RuntimeError("provider down")
        self.sent.append((to, subject, body))


def _stale_ngos(factory, count=2):
    ngos = []
    for i in range(count):
        ngo = factory.ngo(phone=f"+91900000000{i}")
        factory.need(ngo, age_days=40, item_name="Blankets (Single)", category="Bedding")
        factory.need(ngo, age_days=50)
        ngos.append(ngo)
    scan_stale_listings()
    return ngos


def test_dry_run_logs_without_sending(factory):
    _stale_ngos(factory)
    email, whatsapp = FakeNotifier("email"), FakeNotifier("whatsapp")
    summary = send_stale_alerts(dry_run=True, notifiers=[email, whatsapp])

    assert summary["dry_run"] == 4 and summary["sent"] == 0
    assert email.sent == [] and whatsapp.sent == []
    logs = AlertLog.query.all()
    assert {l.status for l in logs} == {"dry_run"} and all(l.item_count == 2 for l in logs)
    assert "Blankets (Single)" in logs[0].message


def test_live_send_groups_items_per_ngo_and_respects_cooldown(factory):
    _stale_ngos(factory)
    email = FakeNotifier("email")
    assert send_stale_alerts(dry_run=False, notifiers=[email])["sent"] == 2
    assert len(email.sent) == 2  # one message per NGO, not per item

    again = send_stale_alerts(dry_run=False, notifiers=[email])
    assert again["skipped_cooldown"] == 2 and len(email.sent) == 2

    later = send_stale_alerts(dry_run=False, notifiers=[email], now=factory.now + timedelta(days=8))
    assert later["sent"] == 2


def test_failing_channel_does_not_block_other_channel_or_ngos(factory):
    _stale_ngos(factory)
    email, whatsapp = FakeNotifier("email", fail=True), FakeNotifier("whatsapp")
    summary = send_stale_alerts(dry_run=False, notifiers=[email, whatsapp])

    assert summary["failed"] == 2 and summary["sent"] == 2
    failed = AlertLog.query.filter_by(status="failed").first()
    assert "provider down" in failed.error

    # A failure alone doesn't start a cooldown, so an NGO no channel reached is retried next run.
    AlertLog.query.filter_by(channel="whatsapp").delete()
    assert send_stale_alerts(dry_run=False, notifiers=[email, whatsapp])["skipped_cooldown"] == 0


def test_unconfigured_channel_is_logged_as_dry_run(factory):
    _stale_ngos(factory, count=1)
    whatsapp = FakeNotifier("whatsapp", configured=False)
    assert send_stale_alerts(dry_run=False, notifiers=[whatsapp])["dry_run"] == 1
    assert whatsapp.sent == []


def test_synthetic_contacts_are_never_messaged_unless_redirected(db, factory):
    ngo = _stale_ngos(factory, count=1)[0]
    ngo.contact_email = "ngo7@smartmatch.test"
    db.session.commit()

    email = FakeNotifier("email")
    assert send_stale_alerts(dry_run=False, notifiers=[email])["dry_run"] == 1
    assert email.sent == []
    assert "synthetic" in AlertLog.query.one().error

    AlertLog.query.delete()
    redirected = FakeNotifier("email", redirect="me@mydomain.in")
    assert send_stale_alerts(dry_run=False, notifiers=[redirected])["sent"] == 1
    assert redirected.sent[0][0] == "me@mydomain.in"
    assert AlertLog.query.one().recipient == "me@mydomain.in (for ngo7@smartmatch.test)"


def test_email_notifier_delivers_over_smtp(monkeypatch):
    """EmailNotifier against a minimal in-process SMTP server (no network, no TLS)."""
    import socketserver
    import threading

    received = {}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            def reply(line):
                self.wfile.write(line.encode() + b"\r\n")

            reply("220 test")
            while True:
                line = self.rfile.readline().decode().strip()
                verb = line.split(" ")[0].upper()
                if verb in ("EHLO", "HELO"):
                    reply("250 test")
                elif verb == "MAIL":
                    received["from"] = line
                    reply("250 ok")
                elif verb == "RCPT":
                    received["to"] = line
                    reply("250 ok")
                elif verb == "DATA":
                    reply("354 go")
                    body = []
                    while (chunk := self.rfile.readline().decode()) not in (".\r\n", ""):
                        body.append(chunk)
                    received["data"] = "".join(body)
                    reply("250 queued")
                elif verb == "QUIT" or not line:
                    reply("221 bye")
                    return
                else:
                    reply("250 ok")

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from smartmatch.alerts import EmailNotifier

        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(server.server_address[1]))
        monkeypatch.setenv("SMTP_FROM", "alerts@smartmatch.test")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        notifier = EmailNotifier()
        assert notifier.configured
        notifier.send("ngo@smartmatch.test", "SmartMatch: please confirm 2 open needs", "Hello")
    finally:
        server.shutdown()
        server.server_close()

    assert "ngo@smartmatch.test" in received["to"]
    assert "Subject: SmartMatch: please confirm 2 open needs" in received["data"]
