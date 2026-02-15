"""Pytest configuration and shared fixtures for Law Scrapper MCP tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
async def mock_client(cache: TTLCache) -> SejmApiClient:
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
