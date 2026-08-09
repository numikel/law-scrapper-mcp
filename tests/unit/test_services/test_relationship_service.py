"""Tests for RelationshipService."""

from unittest.mock import AsyncMock

import pytest

from law_scrapper_mcp.services.relationship_service import RelationshipService


async def test_get_relationships_filters_dictionary_categories() -> None:
    client = AsyncMock()
    client.get_json.return_value = {
        "Akty zmienione": [{"id": 1}],
        "Podstawa prawna": [{"id": 2}],
    }

    result = await RelationshipService(client).get_relationships("DU/2024/1", "Akty zmienione")

    client.get_json.assert_awaited_once_with("acts/DU/2024/1/references")
    assert result.relationships == {"Akty zmienione": [{"id": 1}]}
    assert result.total_count == 1


async def test_get_relationships_normalizes_list_response() -> None:
    client = AsyncMock()
    client.get_json.return_value = [{"id": 1}, {"id": 2}]

    result = await RelationshipService(client).get_relationships("MP/2023/5")

    assert result.relationships == {"references": [{"id": 1}, {"id": 2}]}
    assert result.total_count == 2


async def test_get_relationships_rejects_invalid_eli_before_http() -> None:
    client = AsyncMock()

    with pytest.raises(ValueError, match="Invalid ELI format"):
        await RelationshipService(client).get_relationships("DU/2024")

    client.get_json.assert_not_awaited()


async def test_get_relationships_wraps_scalar_dictionary_value() -> None:
    client = AsyncMock()
    client.get_json.return_value = {"Akty zmienione": {"id": 1}}

    result = await RelationshipService(client).get_relationships("DU/2024/1")

    assert result.relationships == {"Akty zmienione": [{"id": 1}]}
    assert result.total_count == 1


async def test_get_relationships_returns_empty_when_filter_unmatched() -> None:
    client = AsyncMock()
    client.get_json.return_value = {
        "Akty zmienione": [{"id": 1}],
        "Podstawa prawna": [{"id": 2}],
    }

    result = await RelationshipService(client).get_relationships("DU/2024/1", "Akty uchylone")

    assert result.relationships == {}
    assert result.total_count == 0
    assert result.relationship_type == "Akty uchylone"


async def test_get_relationships_returns_empty_for_unexpected_response_shape() -> None:
    client = AsyncMock()
    client.get_json.return_value = "unexpected"

    result = await RelationshipService(client).get_relationships("DU/2024/1")

    assert result.relationships == {}
    assert result.total_count == 0
