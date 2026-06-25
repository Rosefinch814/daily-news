from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from daily_news.storage.supabase import SupabaseStore


@dataclass
class FakeResult:
    data: list[dict[str, Any]] | None = None


class FakeQuery:
    def __init__(self, table_name: str, result_data: list[dict[str, Any]] | None = None) -> None:
        self.table_name = table_name
        self.result_data = result_data if result_data is not None else []
        self.operations: list[tuple[str, Any]] = []

    def insert(self, row: dict[str, Any]) -> "FakeQuery":
        self.operations.append(("insert", row))
        return self

    def select(self, columns: str) -> "FakeQuery":
        self.operations.append(("select", columns))
        return self

    def update(self, row: dict[str, Any]) -> "FakeQuery":
        self.operations.append(("update", row))
        return self

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.operations.append(("eq", (column, value)))
        return self

    def is_(self, column: str, value: Any) -> "FakeQuery":
        self.operations.append(("is", (column, value)))
        return self

    def gte(self, column: str, value: Any) -> "FakeQuery":
        self.operations.append(("gte", (column, value)))
        return self

    def lte(self, column: str, value: Any) -> "FakeQuery":
        self.operations.append(("lte", (column, value)))
        return self

    def in_(self, column: str, values: list[Any]) -> "FakeQuery":
        self.operations.append(("in", (column, values)))
        return self

    def order(self, column: str) -> "FakeQuery":
        self.operations.append(("order", column))
        return self

    def execute(self) -> FakeResult:
        return FakeResult(self.result_data)


class FakeClient:
    def __init__(self, result_data: list[dict[str, Any]] | None = None) -> None:
        self.result_data = result_data if result_data is not None else []
        self.queries: list[FakeQuery] = []

    def table(self, table_name: str) -> FakeQuery:
        query = FakeQuery(table_name, self.result_data)
        self.queries.append(query)
        return query


def test_insert_feedback_writes_expected_payload() -> None:
    client = FakeClient([{"id": "feedback-1"}])
    store = SupabaseStore(client=client)  # type: ignore[arg-type]

    result = store.insert_feedback(
        issue_id="tech-2026-06-25",
        issue_date=date(2026, 6, 25),
        section_slug="tech",
        scope="article",
        article_level="headline",
        article_index=1,
        source_item_ids=["item-1"],
        signal="up",
        note="多看这类芯片供应链新闻",
    )

    assert result == {"id": "feedback-1"}
    query = client.queries[0]
    assert query.table_name == "feedback"
    assert query.operations[0][0] == "insert"
    payload = query.operations[0][1]
    assert payload["issue_date"] == "2026-06-25"
    assert payload["article_index"] == 1
    assert payload["source_item_ids"] == ["item-1"]


def test_fetch_undigested_feedback_filters_by_section_and_date_range() -> None:
    client = FakeClient([{"id": "feedback-1"}])
    store = SupabaseStore(client=client)  # type: ignore[arg-type]

    rows = store.fetch_undigested_feedback(
        "tech",
        from_date=date(2026, 6, 24),
        to_date="2026-06-25",
    )

    assert rows == [{"id": "feedback-1"}]
    operations = client.queries[0].operations
    assert ("select", "*") in operations
    assert ("eq", ("section_slug", "tech")) in operations
    assert ("is", ("digested_at", "null")) in operations
    assert ("gte", ("issue_date", "2026-06-24")) in operations
    assert ("lte", ("issue_date", "2026-06-25")) in operations


def test_fetch_undigested_feedback_can_include_digested_rows() -> None:
    client = FakeClient([])
    store = SupabaseStore(client=client)  # type: ignore[arg-type]

    store.fetch_undigested_feedback("tech", include_digested=True)

    operations = client.queries[0].operations
    assert ("eq", ("section_slug", "tech")) in operations
    assert ("is", ("digested_at", "null")) not in operations


def test_mark_feedback_digested_updates_ids() -> None:
    client = FakeClient([])
    store = SupabaseStore(client=client)  # type: ignore[arg-type]

    store.mark_feedback_digested(["feedback-1", "feedback-2"])

    query = client.queries[0]
    assert query.table_name == "feedback"
    assert query.operations[0][0] == "update"
    assert "digested_at" in query.operations[0][1]
    assert ("in", ("id", ["feedback-1", "feedback-2"])) in query.operations


def test_feedback_methods_noop_without_client() -> None:
    store = SupabaseStore(client=None)

    assert store.insert_feedback(
        issue_id="tech-2026-06-25",
        issue_date="2026-06-25",
        section_slug="tech",
        scope="issue",
    ) is None
    assert store.fetch_undigested_feedback("tech") == []
    store.mark_feedback_digested(["feedback-1"])
