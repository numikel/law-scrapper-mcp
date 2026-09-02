"""`search_legal_acts` validates its page parameters instead of ignoring bad values (#18).

`browse_acts` and every other list tool already fail loudly on a non-integer or negative
`limit`/`offset`. `search_legal_acts` parsed them under `contextlib.suppress`, so
`offset="abc"` quietly became page one, `limit="x"` quietly became twenty, and
`year="abc"` quietly widened the query to the publisher's whole corpus — the
agent asked for one thing and got another with no signal that anything was wrong.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


class TestSearchPageParameterValidation:
    async def test_a_non_integer_offset_is_an_error(self, mcp_client) -> None:
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024, "offset": "abc"})

        assert result.is_error is True
        assert result.structured_content is None
        assert "Parametr 'offset' musi być liczbą całkowitą." in result.content[0].text

    async def test_a_negative_offset_is_an_error(self, mcp_client) -> None:
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024, "offset": -5})

        assert result.is_error is True
        assert result.structured_content is None
        assert "Parametr 'offset' nie może być ujemny." in result.content[0].text

    async def test_a_zero_limit_is_an_error(self, mcp_client) -> None:
        """Unlike the clamped list tools, this one has no use for an empty page: the
        upstream request is the whole cost, and `limit=0` still pays for one record."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024, "limit": 0})

        assert result.is_error is True
        assert result.structured_content is None
        assert "Parametr 'limit' musi być większy od zera." in result.content[0].text

    async def test_a_non_integer_limit_is_an_error(self, mcp_client) -> None:
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024, "limit": "x"})

        assert result.is_error is True
        assert result.structured_content is None
        message = result.content[0].text
        assert "Parametr 'limit' musi być liczbą całkowitą." in message
        assert "invalid literal" not in message

    async def test_a_non_integer_year_is_an_error(self, mcp_client) -> None:
        """The same defect on the parameter that costs the most: a swallowed `year`
        turned a one-year query into a search across the publisher's whole corpus."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": "abc"})

        assert result.is_error is True
        assert result.structured_content is None
        assert "Parametr 'year' musi być liczbą całkowitą." in result.content[0].text

    async def test_a_valid_page_still_works(self, mcp_client) -> None:
        """The guard must not catch the values it exists to protect."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024, "limit": "2", "offset": "1"})

        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["data"]["page_info"]["limit"] == 2
        assert result.structured_content["data"]["page_info"]["offset"] == 1
