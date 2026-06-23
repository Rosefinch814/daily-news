from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR.parents[1]
CONFIG_DIR = WEB_DIR / "config"
TEMPLATES_DIR = WEB_DIR / "templates"
FRONTEND_DIR = WEB_DIR / "frontend"
DIST_DIR = WEB_DIR / "dist"
RUNS_DIR = WEB_DIR / "runs"
