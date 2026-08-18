"""Paths and environment. Imported before anything that reads os.environ."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = Path(os.environ.get("PLANNER_DATA", ROOT / "data"))
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "planner.db"
STATIC = ROOT / "static"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL")
CORE_API_KEY = os.environ.get("CORE_API_KEY")
ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY")

for d in (DATA, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)
