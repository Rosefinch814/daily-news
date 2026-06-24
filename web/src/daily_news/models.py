from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class SourceType(StrEnum):
    RSS = "rss"
    API = "api"


class SourceConfig(BaseModel):
    id: str
    name: str
    type: SourceType = SourceType.RSS
    language: str
    url: HttpUrl
    enabled: bool = True
    weight: float = 1.0
    max_items: int | None = None
    lookback_hours: int | None = None


class TargetRange(BaseModel):
    min: int
    max: int

    @field_validator("max")
    @classmethod
    def max_not_less_than_min(cls, value: int, info: Any) -> int:
        min_value = info.data.get("min")
        if min_value is not None and value < min_value:
            raise ValueError("max must be >= min")
        return value


class WantConfig(BaseModel):
    companies: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)

    @property
    def all_terms(self) -> list[str]:
        return self.companies + self.themes + self.people


class InterestConfig(BaseModel):
    want: WantConfig
    avoid: list[str] = Field(default_factory=list)


class SectionConfig(BaseModel):
    name: str
    publication_name: str
    slug: str
    language: str = "zh-CN"
    issue_volume: int = 1
    target_headlines: TargetRange
    target_briefs: TargetRange
    interests: InterestConfig
    sources: list[SourceConfig]

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [source for source in self.sources if source.enabled]


class AppConfig(BaseModel):
    sections: dict[str, SectionConfig]


class RawItem(BaseModel):
    id: str
    source_id: str
    source_name: str
    source_language: str
    title: str
    url: str
    published_at: datetime | None = None
    summary: str = ""
    content: str = ""
    fetched_at: datetime
    fetch_status: Literal["rss", "content", "failed"] = "rss"
    error: str | None = None

    @property
    def text_for_scoring(self) -> str:
        return " ".join(part for part in [self.title, self.summary, self.content] if part)


class CandidateItem(BaseModel):
    raw_item: RawItem
    score: float
    matched_terms: list[str] = Field(default_factory=list)
    avoided_terms: list[str] = Field(default_factory=list)
    reason: str
    entered_ai: bool = True


class ArticleSource(BaseModel):
    name: str
    url: str


class PullQuote(BaseModel):
    text: str
    cite: str | None = None


class HeadlineArticle(BaseModel):
    source_item_ids: list[str]
    kicker: str
    title_zh: str
    summary_zh: str
    read_body_zh: list[str]
    pullquote: PullQuote | None = None
    ai_impact: str
    sources: list[ArticleSource]
    relevance_score: int = Field(ge=0, le=100)
    importance_score: int = Field(ge=0, le=100)


class BriefArticle(BaseModel):
    source_item_ids: list[str]
    title_zh: str
    summary_zh: str
    sources: list[ArticleSource]
    relevance_score: int = Field(ge=0, le=100)
    importance_score: int = Field(ge=0, le=100)


class DiscardedItem(BaseModel):
    source_item_ids: list[str]
    reason: str
    relevance_score: int = Field(ge=0, le=100)
    importance_score: int = Field(ge=0, le=100)


class MergedSourceEvent(BaseModel):
    source_item_ids: list[str]
    reason: str


class SelectedItem(BaseModel):
    source_item_ids: list[str]
    relevance_score: int = Field(ge=0, le=100)
    importance_score: int = Field(ge=0, le=100)
    reason: str


class CodexShortlistItem(BaseModel):
    source_item_id: str
    decision: Literal["keep", "maybe", "drop"]
    category: str
    relevance_score: int = Field(ge=0, le=100)
    importance_score: int = Field(ge=0, le=100)
    reason: str
    is_aggregate: bool = False
    aggregate_highlights: list[str] = Field(default_factory=list)


class CodexShortlistOutput(BaseModel):
    keep_item_ids: list[str]
    maybe_item_ids: list[str] = Field(default_factory=list)
    drop_item_ids: list[str] = Field(default_factory=list)
    items: list[CodexShortlistItem]

    @field_validator("keep_item_ids")
    @classmethod
    def keep_ids_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("keep_item_ids must not be empty")
        return value


class CodexSelectionOutput(BaseModel):
    headline_item_ids: list[str]
    brief_item_ids: list[str]
    headlines: list[SelectedItem]
    briefs: list[SelectedItem]
    discarded: list[DiscardedItem] = Field(default_factory=list)
    merged_sources: list[MergedSourceEvent] = Field(default_factory=list)

    @field_validator("headline_item_ids")
    @classmethod
    def headline_ids_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("headline_item_ids must not be empty")
        return value

    @field_validator("brief_item_ids")
    @classmethod
    def brief_ids_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("brief_item_ids must not be empty")
        return value


class AIIssueOutput(BaseModel):
    headlines: list[HeadlineArticle]
    briefs: list[BriefArticle]
    discarded: list[DiscardedItem] = Field(default_factory=list)
    merged_sources: list[MergedSourceEvent] = Field(default_factory=list)


class Issue(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    section_slug: str
    publication_name: str
    issue_date: date
    volume: int
    number: int
    date_cn: str
    output_path: str
    headlines: list[HeadlineArticle]
    briefs: list[BriefArticle]
    discarded: list[DiscardedItem] = Field(default_factory=list)
    merged_sources: list[MergedSourceEvent] = Field(default_factory=list)


class AIRunRecord(BaseModel):
    task_type: str
    prompt_version: str
    prompt: str
    raw_output: str
    parsed_output: dict[str, Any] | None = None
    status: Literal["success", "failed"]
    error: str | None = None
    started_at: datetime
    finished_at: datetime
    provider: str | None = None
    model: str | None = None
    attempt_count: int = 1
    repair_used: bool = False
    duration_ms: int | None = None
    command: list[str] = Field(default_factory=list)
    return_code: int | None = None
    prompt_chars: int = 0
    raw_output_chars: int = 0
    parsed_output_chars: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    provider_event_log: str | None = None
    provider_events: str | None = None
