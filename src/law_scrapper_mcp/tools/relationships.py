"""Analyze relationships and references between legal acts."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, RelationshipsOutput
from law_scrapper_mcp.services.response_enrichment import relationships_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register relationships analysis tool."""

    @mcp.tool(meta={"tags": ["analysis", "relationships"]})
    @handle_tool_errors
    async def analyze_act_relationships(
        eli: Annotated[
            str,
            'Identyfikator ELI aktu. Format: "{wydawca}/{rok}/{pozycja}". '
            "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
            'Przykłady: "DU/2024/1716", "MP/2023/500", "DU/2024/1".',
        ],
        ctx: Context[AppContext],
        relationship_type: Annotated[
            str | None,
            "Filtruj po typie powiązania (dokładne dopasowanie do klucza z API). "
            "Dostępne typy: 'Akty zmienione', 'Akty zmieniające', 'Akty uchylone', "
            "'Akty uchylające', 'Akty uznane za uchylone', 'Podstawa prawna', "
            "'Podstawa prawna z art.', 'Teksty jednolite'. "
            "None = zwróć wszystkie powiązania.",
        ] = None,
    ) -> EnrichedResponse[RelationshipsOutput]:
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
        output = await get_app_context(ctx).relationship_service.get_relationships(eli, relationship_type)
        return EnrichedResponse[RelationshipsOutput](
            data=output,
            hints=relationships_hints(eli, list(output.relationships)),
        )
