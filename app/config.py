"""Central configuration. Everything can be overridden with environment variables."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("VMS_DATA_DIR", BASE_DIR / "data"))
SNAP_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "vms.sqlite3"
MODEL_ROOT = Path(os.getenv("VMS_MODEL_ROOT", DATA_DIR / "insightface"))
MODEL_PACK = os.getenv("VMS_MODEL_PACK", "buffalo_l")  # buffalo_l (accurate) | buffalo_s / buffalo_sc (faster)

HOST = os.getenv("VMS_HOST", "0.0.0.0")
PORT = int(os.getenv("VMS_PORT", "8081"))
BASE_URL = os.getenv("VMS_BASE_URL", f"http://localhost:{PORT}").rstrip("/")

ADMIN_USER = os.getenv("VMS_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("VMS_ADMIN_PASSWORD", "")  # empty -> random one is generated & printed at start

# Optional notifications
SMTP_HOST = os.getenv("VMS_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("VMS_SMTP_PORT", "587"))
SMTP_USER = os.getenv("VMS_SMTP_USER", "")
SMTP_PASSWORD = os.getenv("VMS_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("VMS_SMTP_FROM", SMTP_USER)
SECURITY_EMAIL = os.getenv("VMS_SECURITY_EMAIL", "")      # gets "blocked person at the door" alerts
WEBHOOK_URL = os.getenv("VMS_WEBHOOK_URL", "")            # Slack / Discord / Teams-style JSON webhook

# Tunable defaults (editable live from Settings page, stored in DB)
DEFAULT_SETTINGS = {
    "match_threshold": 0.45,     # cosine similarity needed to accept an identity (ArcFace)
    "match_margin": 0.05,        # best match must beat the runner-up by this much
    "min_face_px": 90,           # smallest face (short side, px) accepted
    "blur_min": 35,              # Laplacian variance floor on normalised crop
    "frames_required": 2,        # consistent good frames needed before a decision
    "cooldown_seconds": 60,      # ignore repeat check-ins by the same person within this window
    "approval_timeout_min": 10,  # pending requests expire after this long
    "retention_days": 90,        # delete visit snapshots older than this (0 = keep forever)
    "org_name": "Front Desk",
}


def secret_key() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / ".secret_key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p.read_text().strip()
