"""K14: every tool is classified against the shared pagination contract.

The registry is the point of the test. A new tool that returns a list must
either join `PAGINATED` or carry a written reason for staying out — it cannot
slip in unclassified.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from law_scrapper_mcp.models.tool_outputs import (
    ChangesOutput,
    ContentOutput,
    FilterOutput,
    LoadedDocumentListOutput,
    MetadataOutput,
    ResultSetListOutput,
    SearchInActOutput,
    SearchOutput,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

PAGINATED: dict[str, type[BaseModel]] = {
    "get_system_metadata": MetadataOutput,
    "read_act_content": ContentOutput,
    "search_in_act": SearchInActOutput,
    "track_legal_changes": ChangesOutput,
    "filter_results": FilterOutput,
    "list_result_sets": ResultSetListOutput,
    "list_loaded_documents": LoadedDocumentListOutput,
    "search_legal_acts": SearchOutput,
    "browse_acts": SearchOutput,
}

NO_LIST_PAYLOAD: dict[str, str] = {
    "calculate_legal_date": "DateOutput zwraca pojedynczą datę — brak ładunku o zmiennej długości.",
    "analyze_act_relationships": (
        "RelationshipsOutput zwraca mapę typów powiązań o rozmiarze wyznaczonym przez API, "
        "nie przez zapytanie wywołującego."
    ),
    "compare_acts": "CompareOutput porównuje dokładnie dwa akty — rozmiar wyniku jest stały.",
    "get_act_details": (
        "ActDetailOutput opisuje jeden akt; pole toc jest ograniczone liczbą sekcji tego aktu, "
        "a stronicowany spis treści udostępnia read_act_content."
    ),
}

# Empty by design after Task 8 pulled SearchOutput into the shared model.
# The bucket stays so that a future tool can only opt out in writing.
DOCUMENTED_EXEMPTIONS: dict[str, str] = {}


async def test_every_tool_is_classified(mcp_client) -> None:  # type: ignore[no-untyped-def]
    listed = {tool.name for tool in (await mcp_client.list_tools()).tools}
    classified = set(PAGINATED) | set(NO_LIST_PAYLOAD) | set(DOCUMENTED_EXEMPTIONS)

    assert listed == classified, (
        f"unclassified tools: {sorted(listed - classified)}; stale registry entries: {sorted(classified - listed)}"
    )
    assert len(listed) == 13


async def test_no_tool_is_classified_twice() -> None:
    buckets = (set(PAGINATED), set(NO_LIST_PAYLOAD), set(DOCUMENTED_EXEMPTIONS))
    assert sum(len(bucket) for bucket in buckets) == len(set().union(*buckets))


async def test_every_exemption_carries_a_reason() -> None:
    for name, reason in (NO_LIST_PAYLOAD | DOCUMENTED_EXEMPTIONS).items():
        assert len(reason) > 40, f"{name} needs a written reason, not a placeholder"


@pytest.mark.parametrize(
    "model_cls",
    sorted(set(PAGINATED.values()), key=lambda cls: cls.__name__),  # type: ignore[attr-defined]
)
async def test_paginated_models_reject_a_missing_page_info(model_cls: type[BaseModel]) -> None:
    fields = {name: info for name, info in model_cls.model_fields.items() if name != "page_info" and info.is_required()}
    payload = {name: _placeholder(info.annotation) for name, info in fields.items()}

    with pytest.raises(ValidationError) as exc_info:
        model_cls(**payload)
    assert any(err["loc"] == ("page_info",) and err["type"] == "missing" for err in exc_info.value.errors()), (
        f"{model_cls.__name__} failed validation for an unrelated field: {exc_info.value.errors()}"
    )


def _placeholder(annotation: object) -> object:
    text = str(annotation)
    if "list" in text:
        return []
    if "dict" in text:
        return {}
    if "int" in text:
        return 0
    return "x"
