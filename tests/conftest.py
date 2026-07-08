"""Pytest configuration and shared fixtures for Law Scrapper MCP tests."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the fixtures directory path."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_act_html(fixtures_dir: Path) -> str:
    """Load sample HTML act content."""
    return (fixtures_dir / "sample_act.html").read_text(encoding="utf-8")


@pytest.fixture
def search_results(fixtures_dir: Path) -> dict:
    """Load search results fixture."""
    return json.loads((fixtures_dir / "search_results.json").read_text(encoding="utf-8"))


@pytest.fixture
def act_detail(fixtures_dir: Path) -> dict:
    """Load act detail fixture."""
    return json.loads((fixtures_dir / "act_detail.json").read_text(encoding="utf-8"))


@pytest.fixture
def act_structure(fixtures_dir: Path) -> list:
    """Load act structure fixture."""
    return json.loads((fixtures_dir / "act_structure.json").read_text(encoding="utf-8"))


@pytest.fixture
def act_references(fixtures_dir: Path) -> dict:
    """Load act references fixture."""
    return json.loads((fixtures_dir / "act_references.json").read_text(encoding="utf-8"))


@pytest.fixture
def publishers_data(fixtures_dir: Path) -> list:
    """Load publishers fixture."""
    return json.loads((fixtures_dir / "publishers.json").read_text(encoding="utf-8"))


@pytest.fixture
def cache() -> TTLCache:
    """Create a fresh TTLCache instance."""
    return TTLCache(max_entries=100)


@pytest.fixture
async def mock_client(cache: TTLCache) -> AsyncGenerator[SejmApiClient]:
    """Create a SejmApiClient with mocked httpx.

    Note: Tests using this fixture should use respx to mock HTTP responses.
    """
    client = SejmApiClient(cache=cache, timeout=30.0, max_concurrent=10)
    await client.start()
    yield client
    await client.close()


@pytest.fixture
def document_store() -> DocumentStore:
    """Create a fresh DocumentStore instance."""
    return DocumentStore(max_documents=10, max_size_bytes=5 * 1024 * 1024, ttl=7200)


@pytest.fixture
def content_processor() -> ContentProcessor:
    """Create a ContentProcessor instance."""
    return ContentProcessor()


@pytest.fixture
def mock_api_responses(
    publishers_data: list,
    search_results: dict,
    act_detail: dict,
    act_references: dict,
):
    """Setup common API response mocks for integration tests."""
    act_detail_2 = {
        "ELI": "DU/2024/2",
        "publisher": "DU",
        "year": 2024,
        "pos": 2,
        "title": "Rozporządzenie testowe 2",
        "status": "akt obowiązujący",
        "type": "Rozporządzenie",
        "announcementDate": "2024-01-10",
        "promulgation": "2024-01-10",
        "entryIntoForce": "2024-03-01",
        "validFrom": "2024-03-01",
        "repealDate": None,
        "changeDate": None,
        "keywords": ["transport"],
        "references": None,
        "volume": 1,
        "textPDF": "/eli/acts/DU/2024/2/text.pdf",
        "textHTML": None,
        "inForce": "YES",
    }
    with respx.mock:
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(return_value=Response(200, json=publishers_data))
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(
            return_value=Response(200, json=["prawo", "ustawa", "kodeks"])
        )
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(
            return_value=Response(200, json=["akt obowiązujący", "uchylony"])
        )
        respx.get("https://api.sejm.gov.pl/eli/types").mock(
            return_value=Response(200, json=["Ustawa", "Rozporządzenie"])
        )
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(
            return_value=Response(200, json=["Sejm RP", "Senat RP"])
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024").mock(return_value=Response(200, json=search_results))
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail))
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(return_value=Response(404))
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.html").mock(
            return_value=Response(
                200,
                text="<html><body><h1>Test Act</h1><p>Art. 1. Content here.</p></body></html>",
            )
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/references").mock(
            return_value=Response(200, json=act_references)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/2").mock(return_value=Response(200, json=act_detail_2))
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/2/struct").mock(return_value=Response(404))
        yield


@pytest.fixture
async def mcp_client(mock_api_responses):
    """FastMCP in-memory client with mocked Sejm API responses."""
    from fastmcp import Client

    from law_scrapper_mcp.server import app

    async with Client(app) as client:
        yield client
