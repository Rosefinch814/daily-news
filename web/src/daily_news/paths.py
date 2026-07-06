from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR.parents[1]
CONFIG_DIR = WEB_DIR / "config"
FRONTEND_DIR = WEB_DIR / "frontend"
DIST_DIR = WEB_DIR / "dist"
DIST_OWNER_DIR = WEB_DIR / "dist-owner"
RUNS_DIR = WEB_DIR / "runs"
LOGS_DIR = WEB_DIR / "logs"
PROFILES_DIR = WEB_DIR / "profiles"
