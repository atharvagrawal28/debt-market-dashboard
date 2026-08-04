"""Shared paths and constants."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "market.db")

# Legacy single-blob DB from the first version of the app. Migrated on first run.
LEGACY_DB_PATH = os.path.join(BASE_DIR, "reports.db")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT = 60

for _d in (DATA_DIR, RAW_DIR):
    os.makedirs(_d, exist_ok=True)
