#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
# Optional: export VMS_ADMIN_PASSWORD=... VMS_SMTP_HOST=... VMS_WEBHOOK_URL=...  (see README)
python -m app.server
