from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from supabase import Client, create_client

from daily_news.models import AIRunRecord, CandidateItem, Issue, RawItem, SectionConfig


@dataclass
class SupabaseStore:
    client: Client | None

    @classmethod
    def from_env(cls) -> "SupabaseStore":
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return cls(client=None)
        return cls(client=create_client(url, key))

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def upsert_sources(self, section: SectionConfig) -> None:
        if not self.client:
            return
        rows = [
            {
                "id": source.id,
                "section_slug": section.slug,
                "name": source.name,
                "url": str(source.url),
                "language": source.language,
                "type": source.type.value,
                "enabled": source.enabled,
                "weight": source.weight,
            }
            for source in section.sources
        ]
        self.client.table("sources").upsert(rows).execute()

    def create_fetch_run(self, run_id: str, section: SectionConfig, issue_date: str) -> None:
        if not self.client:
            return
        self.client.table("fetch_runs").upsert(
            {
                "id": run_id,
                "section_slug": section.slug,
                "issue_date": issue_date,
                "status": "running",
            }
        ).execute()

    def finish_fetch_run(self, run_id: str, *, status: str, error: str | None = None) -> None:
        if not self.client:
            return
        self.client.table("fetch_runs").update({"status": status, "error": error}).eq("id", run_id).execute()

    def insert_raw_items(self, run_id: str, raw_items: list[RawItem]) -> None:
        if not self.client or not raw_items:
            return
        rows = []
        for item in raw_items:
            rows.append(
                {
                    "id": item.id,
                    "fetch_run_id": run_id,
                    "source_id": item.source_id,
                    "source_name": item.source_name,
                    "source_language": item.source_language,
                    "title": item.title,
                    "url": item.url,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary": item.summary,
                    "content": item.content,
                    "fetch_status": item.fetch_status,
                    "error": item.error,
                    "fetched_at": item.fetched_at.isoformat(),
                }
            )
        self.client.table("raw_items").upsert(rows).execute()

    def insert_candidates(self, run_id: str, candidates: list[CandidateItem]) -> None:
        if not self.client or not candidates:
            return
        rows = [
            {
                "fetch_run_id": run_id,
                "raw_item_id": candidate.raw_item.id,
                "score": candidate.score,
                "matched_terms": candidate.matched_terms,
                "avoided_terms": candidate.avoided_terms,
                "reason": candidate.reason,
                "entered_ai": candidate.entered_ai,
            }
            for candidate in candidates
        ]
        self.client.table("candidates").insert(rows).execute()

    def insert_ai_run(self, run_id: str, ai_run: AIRunRecord) -> None:
        if not self.client:
            return
        self.client.table("ai_runs").insert(
            {
                "fetch_run_id": run_id,
                "task_type": ai_run.task_type,
                "prompt_version": ai_run.prompt_version,
                "prompt": ai_run.prompt,
                "raw_output": ai_run.raw_output,
                "parsed_output": ai_run.parsed_output,
                "status": ai_run.status,
                "error": ai_run.error,
                "started_at": ai_run.started_at.isoformat(),
                "finished_at": ai_run.finished_at.isoformat(),
            }
        ).execute()

    def insert_feedback(
        self,
        *,
        issue_id: str,
        issue_date: str | date,
        section_slug: str,
        scope: str,
        article_level: str | None = None,
        article_index: int | None = None,
        source_item_ids: list[str] | None = None,
        signal: str | None = None,
        note: str | None = None,
        owner_token: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.client:
            return None
        row = {
            "issue_id": issue_id,
            "issue_date": issue_date.isoformat() if isinstance(issue_date, date) else issue_date,
            "section_slug": section_slug,
            "scope": scope,
            "article_level": article_level,
            "article_index": article_index,
            "source_item_ids": source_item_ids or [],
            "signal": signal,
            "note": note,
            "owner_token": owner_token,
        }
        result = self.client.table("feedback").insert(row).execute()
        data = getattr(result, "data", None)
        if isinstance(data, list) and data:
            return data[0]
        return None

    def insert_product_feedback(
        self,
        *,
        issue_id: str | None = None,
        issue_date: str | date | None = None,
        section_slug: str | None = None,
        note: str,
    ) -> dict[str, Any] | None:
        if not self.client:
            return None
        row = {
            "issue_id": issue_id,
            "issue_date": issue_date.isoformat() if isinstance(issue_date, date) else issue_date,
            "section_slug": section_slug,
            "note": note,
        }
        result = self.client.table("product_feedback").insert(row).execute()
        data = getattr(result, "data", None)
        if isinstance(data, list) and data:
            return data[0]
        return None

    def fetch_undigested_feedback(
        self,
        section_slug: str,
        *,
        from_date: str | date | None = None,
        to_date: str | date | None = None,
        include_digested: bool = False,
        owner_token: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.client:
            return []
        query = self.client.table("feedback").select("*").eq("section_slug", section_slug)
        if owner_token is not None:
            query = query.eq("owner_token", owner_token)
        if not include_digested:
            query = query.is_("digested_at", "null")
        if from_date:
            value = from_date.isoformat() if isinstance(from_date, date) else from_date
            query = query.gte("issue_date", value)
        if to_date:
            value = to_date.isoformat() if isinstance(to_date, date) else to_date
            query = query.lte("issue_date", value)
        result = query.order("issue_date").order("created_at").execute()
        data = getattr(result, "data", None)
        return data if isinstance(data, list) else []

    def mark_feedback_digested(self, ids: list[str]) -> None:
        if not self.client or not ids:
            return
        self.client.table("feedback").update(
            {"digested_at": datetime.now(timezone.utc).isoformat()}
        ).in_("id", ids).execute()

    def insert_issue(self, run_id: str, issue: Issue) -> None:
        if not self.client:
            return
        self.client.table("issues").upsert(
            {
                "id": issue.id,
                "fetch_run_id": run_id,
                "section_slug": issue.section_slug,
                "publication_name": issue.publication_name,
                "issue_date": issue.issue_date.isoformat(),
                "volume": issue.volume,
                "number": issue.number,
                "html_path": issue.output_path,
                "status": "generated",
            }
        ).execute()

        rows: list[dict[str, Any]] = []
        for index, article in enumerate(issue.headlines, start=1):
            rows.append(
                {
                    "issue_id": issue.id,
                    "article_no": index,
                    "level": "headline",
                    "title_zh": article.title_zh,
                    "summary_zh": article.summary_zh,
                    "read_body_zh": article.read_body_zh,
                    "ai_impact": article.ai_impact,
                    "sources": [source.model_dump(mode="json") for source in article.sources],
                    "source_item_ids": article.source_item_ids,
                    "relevance_score": article.relevance_score,
                    "importance_score": article.importance_score,
                }
            )
        offset = len(issue.headlines)
        for index, article in enumerate(issue.briefs, start=1):
            rows.append(
                {
                    "issue_id": issue.id,
                    "article_no": offset + index,
                    "level": "brief",
                    "title_zh": article.title_zh,
                    "summary_zh": article.summary_zh,
                    "read_body_zh": [],
                    "ai_impact": None,
                    "sources": [source.model_dump(mode="json") for source in article.sources],
                    "source_item_ids": article.source_item_ids,
                    "relevance_score": article.relevance_score,
                    "importance_score": article.importance_score,
                }
            )
        if rows:
            self.client.table("issue_articles").insert(rows).execute()
