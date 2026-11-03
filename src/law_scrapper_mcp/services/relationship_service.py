"""Domain service for legal act relationships."""

from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.tool_inputs import parse_eli
from law_scrapper_mcp.models.tool_outputs import RelationshipsOutput
from law_scrapper_mcp.services.act_sources import ActSourceRegistry


class RelationshipService:
    """Retrieve and filter relationships for a legal act."""

    def __init__(self, client: SejmApiClient, *, sources: ActSourceRegistry | None = None) -> None:
        self._client = client
        self._sources = sources if sources is not None else ActSourceRegistry()

    async def get_relationships(
        self,
        eli: str,
        relationship_type: str | None = None,
    ) -> RelationshipsOutput:
        publisher, year, pos = parse_eli(eli)
        source = self._sources.for_publisher(publisher)
        raw: Any
        if source is not None:
            raw = await source.get_relationships(publisher, year, pos)
        else:
            raw = await self._client.get_json(f"acts/{publisher}/{year}/{pos}/references")
        relationships: dict[str, Any] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if relationship_type is None or key == relationship_type:
                    relationships[key] = value if isinstance(value, list) else [value]
        elif isinstance(raw, list):
            relationships["references"] = raw
        return RelationshipsOutput(
            eli=eli,
            relationship_type=relationship_type,
            relationships=relationships,
            total_count=sum(len(value) for value in relationships.values()),
        )
