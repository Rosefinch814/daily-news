from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from daily_news.models import AppConfig, SectionConfig
from daily_news.paths import CONFIG_DIR


class ProviderRuntimeConfig(BaseModel):
    command: str
    model: str | None = None
    max_budget_usd: float | None = None


class PipelineAIConfig(BaseModel):
    default_provider: Literal["claude", "codex"] = "codex"
    stage_providers: dict[Literal["semantic_shortlist", "selection", "issue_compose"], Literal["claude", "codex"]] = Field(
        default_factory=dict
    )
    timeout_seconds: int = 300
    repair_attempts: int = 1
    claude: ProviderRuntimeConfig = Field(default_factory=lambda: ProviderRuntimeConfig(command="claude -p"))
    codex: ProviderRuntimeConfig = Field(default_factory=lambda: ProviderRuntimeConfig(command="codex exec"))


class PipelinePromptConfig(BaseModel):
    max_summary_chars: int = 650
    max_content_chars: int = 1200
    max_candidates: int = 60


class PipelineLoggingConfig(BaseModel):
    save_provider_events: bool = True
    save_attempts: bool = True
    append_metrics_jsonl: bool = True
    redact_command: bool = True


class PipelineConfig(BaseModel):
    ai: PipelineAIConfig = Field(default_factory=PipelineAIConfig)
    prompt: PipelinePromptConfig = Field(default_factory=PipelinePromptConfig)
    logging: PipelineLoggingConfig = Field(default_factory=PipelineLoggingConfig)


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


def load_pipeline_config(path: Path | None = None) -> PipelineConfig:
    config_path = path or CONFIG_DIR / "pipeline.yaml"
    if not config_path.exists():
        return PipelineConfig()
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return PipelineConfig.model_validate(data)
