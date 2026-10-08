import os
from pathlib import Path

REBRICKABLE_API_KEY = os.environ.get("REBRICKABLE_API_KEY", "")
BRICKOWL_API_KEY = os.environ.get("BRICKOWL_API_KEY", "")
APP_PASSCODE = os.environ.get("APP_PASSCODE", "")
# Railway volume is mounted at /data; fall back to a local folder for development.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data" if Path("/data").is_dir() else "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "lego_buyer.sqlite3"
PORT = int(os.environ.get("PORT", "8000"))
SECRET = os.environ.get("APP_SECRET", APP_PASSCODE or "dev-secret")
