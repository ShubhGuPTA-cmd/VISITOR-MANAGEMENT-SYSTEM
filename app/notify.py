"""Fire-and-forget notifications (email + webhook). Never blocks or breaks a request."""
import json
import logging
import smtplib
import threading
import urllib.request
from email.message import EmailMessage

from . import config

log = logging.getLogger("vms.notify")


def _run(fn, *a):
    threading.Thread(target=lambda: _safe(fn, *a), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:  # noqa: BLE001
        log.warning("notification failed: %s", e)


def _email(to: str, subject: str, body: str, image: bytes = None):
    if not (config.SMTP_HOST and to):
        return
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = config.SMTP_FROM or config.SMTP_USER, to, subject
    m.set_content(body)
    if image:
        m.add_attachment(image, maintype="image", subtype="jpeg", filename="visitor.jpg")
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(m)


def _webhook(text: str):
    if not config.WEBHOOK_URL:
        return
    data = json.dumps({"text": text, "content": text}).encode()
    req = urllib.request.Request(config.WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def visitor_waiting(visit: dict, host: dict, image: bytes = None):
    link = f"{config.BASE_URL}/approve/{visit['token']}"
    text = (f"{visit['name']} is at the front desk for {host.get('name', 'reception')}.\n"
            f"Purpose: {visit['purpose'] or '-'}\nApprove or deny: {link}")
    if host.get("email"):
        _run(_email, host["email"], f"Visitor waiting: {visit['name']}", text, image)
    _run(_webhook, text)


def blocked_attempt(person: dict, score: float):
    text = f"ALERT: blocked person '{person['name']}' was recognised at the entrance (match {score:.2f})."
    _run(_email, config.SECURITY_EMAIL, "Blocked person at entrance", text)
    _run(_webhook, text)
