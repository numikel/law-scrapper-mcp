"""Analyze relationships and references between legal acts."""

import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, RelationshipsOutput
from law_scrapper_mcp.services.response_enrichment import relationships_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register relationships analysis tool."""

    @mcp.tool(tags={"analysis", "relationships"})
    @handle_tool_errors(
        default_factory=lambda e, kw: RelationshipsOutput(
            eli=kw.get("eli", ""),
            relationship_type=kw.get("relationship_type"),
            relationships={},
            total_count=0,
        ),
    )
    async def analyze_act_relationships(
        eli: Annotated[
            str,
            "Identyfikator ELI aktu. Format: \"{wydawca}/{rok}/{pozycja}\". "
            "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
            "Przykłady: \"DU/2024/1716\", \"MP/2023/500\", \"DU/2024/1\".",
        ],
        relationship_type: Annotated[
            str | None,
            "Filtruj po typie powiązania (dokładne dopasowanie do klucza z API). "
            "Dostępne typy: 'Akty zmienione', 'Akty zmieniające', 'Akty uchylone', "
            "'Akty uchylające', 'Akty uznane za uchylone', 'Podstawa prawna', "
            "'Podstawa prawna z art.', 'Teksty jednolite'. "
            "None = zwróć wszystkie powiązania.",
        ] = None,
        ctx: Context = None,
    ) -> str:
        """
        Przeanalizuj powiązania i referencje między aktami prawnymi.

        Zwraca informacje o aktach zmienionych, zmieniających, uchylonych,
        podstawie prawnej i tekstach jednolitych.

        Przykłady:
        - analyze_act_relationships(eli="DU/2024/1716") - Wszystkie powiązania
        - analyze_act_relationships(eli="DU/2024/1716", relationship_type="Akty zmienione") - Jakie akty zmienił
        - analyze_act_relationships(eli="DU/2024/1716", relationship_type="Podstawa prawna") - Na jakiej podstawie powstał
        - analyze_act_relationships(eli="DU/2024/1716", relationship_type="Akty zmieniające") - Co go zmienia
        - analyze_act_relationships(eli="DU/2024/1", relationship_type="Akty uznane za uchylone") - Uchylone akty
        """
        assert ctx is not None
        client = ctx.request_context.lifespan_context["client"]

        # Parse ELI
        parts = eli.split("/")
        if len(parts) != 3:
            raise ValueError(f"Nieprawidłowy format ELI: {eli}. Oczekiwany: wydawca/rok/pozycja")
        publisher, year, pos = parts

        # Get references
        references_data = await client.get_json(
            f"acts/{publisher}/{year}/{pos}/references"
        )

        # Process relationships
        relationships = {}
        if isinstance(references_data, dict):
            for key, value in references_data.items():
                if relationship_type is None or key == relationship_type:
                    relationships[key] = value if isinstance(value, list) else [value]
        elif isinstance(references_data, list):
            relationships["references"] = references_data

        total_count = sum(
            len(v) if isinstance(v, list) else 1 for v in relationships.values()
        )

        response = EnrichedResponse(
            data=RelationshipsOutput(
                eli=eli,
                relationship_type=relationship_type,
                relationships=relationships,
                total_count=total_count,
            ),
            hints=relationships_hints(eli, list(relationships.keys())),
        )

        return response.model_dump_json()
