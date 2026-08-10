"""Compare metadata of two legal acts."""

import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import CompareOutput, EnrichedResponse
from law_scrapper_mcp.services.response_enrichment import compare_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register compare acts tool."""

    @mcp.tool(tags={"analysis", "compare"})
    @handle_tool_errors(
        default_factory=lambda e, kw: CompareOutput(
            eli_a=kw.get("eli_a", ""),
            eli_b=kw.get("eli_b", ""),
            comparison={},
            common_keywords=[],
            differences=[],
        ),
    )
    async def compare_acts(
        eli_a: Annotated[
            str,
            'Identyfikator ELI pierwszego aktu. Format: "{wydawca}/{rok}/{pozycja}". '
            'Przykłady: "DU/2024/1716", "MP/2023/500".',
        ],
        eli_b: Annotated[
            str,
            "Identyfikator ELI drugiego aktu do porównania. "
            'Format: "{wydawca}/{rok}/{pozycja}". '
            'Przykłady: "DU/2024/1692", "DU/2020/1444".',
        ],
        ctx: Context = None,
    ) -> str:
        """
        Porównaj metadane dwóch aktów prawnych.

        Porównuje tytuły, typy, statusy, daty (ogłoszenia, promulgacji, wejścia w życie),
        słowa kluczowe dwóch aktów. Identyfikuje różnice i wspólne elementy.
        Nie wymaga wcześniejszego załadowania treści (load_content).

        Kiedy użyć: Aby porównać metadane dwóch aktów (np. wersji, powiązanych aktów).
        Kiedy NIE używać: Do porównania treści → załaduj oba akty i użyj read_act_content.

        Przykłady:
        - compare_acts(eli_a="DU/2024/1692", eli_b="DU/2024/1716") - Porównaj dwa akty z 2024
        - compare_acts(eli_a="DU/2020/1444", eli_b="DU/2024/1692") - Porównaj starą i nową wersję
        - compare_acts(eli_a="DU/2024/1", eli_b="DU/2023/2000") - Porównaj akty z różnych lat
        - compare_acts(eli_a="DU/2024/100", eli_b="MP/2024/200") - Porównaj DU vs MP
        - compare_acts(eli_a="DU/2021/1500", eli_b="DU/2021/1600") - Porównaj podobne akty
        """
        assert ctx is not None
        output = await ctx.lifespan_context.comparison_service.compare(eli_a, eli_b)
        return EnrichedResponse(data=output, hints=compare_hints(eli_a, eli_b)).model_dump_json()
