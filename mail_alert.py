#!/usr/bin/env python3
"""QQ Bot Email Alert - Send alerts via QQ Mail SMTP"""
import smtplib
import sys
import os
import time
from pathlib import Path
from email.mime.text import MIMEText
from email.utils import formatdate
from dotenv import load_dotenv

# watchdog starts this script directly, so it must load the project .env itself.
load_dotenv(Path(__file__).with_name(".env"))

# === Config (loaded from env or hardcoded defaults) ===
SMTP_SERVER = os.environ.get("ALERT_SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("ALERT_SMTP_PORT", "465"))
SMTP_USER = os.environ.get("ALERT_SMTP_USER", "")
SMTP_PASS = os.environ.get("ALERT_SMTP_PASS", "")  # QQ邮箱授权码，非登录密码
ALERT_TO = os.environ.get("ALERT_TO_EMAIL", "your-email@example.com")

# Cooldown: same alert type won't repeat within 30 minutes
COOLDOWN_DIR = "/tmp"
COOLDOWN_SECONDS = 1800

def check_cooldown(alert_type: str) -> bool:
    ts_file = os.path.join(COOLDOWN_DIR, f"qqbot_mail_{alert_type}.ts")
    try:
        with open(ts_file) as f:
            last_ts = float(f.read().strip())
        if time.time() - last_ts < COOLDOWN_SECONDS:
            return False  # Still in cooldown
    except (FileNotFoundError, ValueError):
        pass
    return True

def record_cooldown(alert_type: str) -> None:
    ts_file = os.path.join(COOLDOWN_DIR, f"qqbot_mail_{alert_type}.ts")
    with open(ts_file, "w") as f:
        f.write(str(time.time()))

def send_email(subject: str, body: str) -> bool:
    if not SMTP_USER or not SMTP_PASS:
        print("[MailAlert] ERROR: ALERT_SMTP_USER or ALERT_SMTP_PASS not set", file=sys.stderr)
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_TO
    msg["Date"] = formatdate(localtime=True)

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [ALERT_TO], msg.as_string())
        print(f"[MailAlert] SENT: {subject}")
        return True
    except Exception as e:
        print(f"[MailAlert] FAILED: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: mail_alert.py ALERT_TYPE SUBJECT [BODY]", file=sys.stderr)
        sys.exit(1)

    alert_type = sys.argv[1]
    subject = sys.argv[2]
    body = sys.argv[3] if len(sys.argv) > 3 else subject

    if not check_cooldown(alert_type):
        print(f"[MailAlert] SKIP: {alert_type} (cooldown active)")
        sys.exit(0)

    ok = send_email(subject, body)
    if ok:
        record_cooldown(alert_type)
    sys.exit(0 if ok else 1)
