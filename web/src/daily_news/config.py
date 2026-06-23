from __future__ import annotations

from pathlib import Path

import yaml

from daily_news.models import AppConfig, SectionConfig
from daily_news.paths import CONFIG_DIR


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or CONFIG_DIR / "sections.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return AppConfig.model_validate(data)


def load_section(section_slug: str, path: Path | None = None) -> SectionConfig:
    config = load_config(path)
    try:
        return config.sections[section_slug]
    except KeyError as exc:
        available = ", ".join(sorted(config.sections))
        raise ValueError(f"Unknown section '{section_slug}'. Available: {available}") from exc
