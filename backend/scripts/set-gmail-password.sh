#!/usr/bin/env bash
# Local helper: store the Gmail APP PASSWORD silently (never echoed / not in shell history) and verify the login.
# No email is sent. On the cloud server use the app instead (Settings > Gmail app password).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
F="${JOBHUB_SECRETS_DIR:-$ROOT/.secrets}/gmail_app_password"
USER_EMAIL="$(python3 - "$ROOT" <<'PY'
import sys, yaml, os, pathlib
root = pathlib.Path(sys.argv[1])
p = pathlib.Path(os.environ.get("JOBHUB_PROFILE") or root / "profile.yaml")
print(yaml.safe_load(p.read_text())["hr_outreach"]["gmail_user"])
PY
)"
mkdir -p "$(dirname "$F")"; chmod 700 "$(dirname "$F")"
read -r -s -p "Paste the 16-char Gmail app password for $USER_EMAIL: " PW; echo
umask 077; printf '%s' "${PW// /}" > "$F"; chmod 600 "$F"
GMAIL_USER="$USER_EMAIL" SECRET_FILE="$F" python3 - <<'PY'
import os, smtplib, ssl, pathlib
pw = pathlib.Path(os.environ["SECRET_FILE"]).read_text().strip()
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as s:
        s.login(os.environ["GMAIL_USER"], pw)
    print("OK: Gmail login works (no email sent).")
except smtplib.SMTPAuthenticationError:
    print("FAILED: Google rejected it. Check 2-Step Verification is on and you pasted the app password, not your login password.")
    raise SystemExit(1)
PY
