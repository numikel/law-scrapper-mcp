"""Compare metadata of two legal acts."""

import logging
from typing import Annotated, Any

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
            "Identyfikator ELI pierwszego aktu. Format: \"{wydawca}/{rok}/{pozycja}\". "
            "Przykłady: \"DU/2024/1716\", \"MP/2023/500\".",
        ],
        eli_b: Annotated[
            str,
            "Identyfikator ELI drugiego aktu do porównania. "
            "Format: \"{wydawca}/{rok}/{pozycja}\". "
            "Przykłady: \"DU/2024/1692\", \"DU/2020/1444\".",
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
        act_service = ctx.request_context.lifespan_context["act_service"]

        # Fetch details for both acts (no content loading needed)
        details_a = await act_service.get_details(eli=eli_a, load_content=False)
        details_b = await act_service.get_details(eli=eli_b, load_content=False)

        # Build comparison dict
        comparison: dict[str, Any] = {
            "title_a": details_a.title,
            "title_b": details_b.title,
            "type_a": details_a.type or "N/A",
            "type_b": details_b.type or "N/A",
            "status_a": details_a.status,
            "status_b": details_b.status,
            "announcement_date_a": details_a.announcement_date or "N/A",
            "announcement_date_b": details_b.announcement_date or "N/A",
            "promulgation_date_a": details_a.promulgation_date or "N/A",
            "promulgation_date_b": details_b.promulgation_date or "N/A",
            "entry_into_force_a": details_a.entry_into_force or "N/A",
            "entry_into_force_b": details_b.entry_into_force or "N/A",
            "keywords_a": details_a.keywords,
            "keywords_b": details_b.keywords,
        }

        # Calculate common keywords
        set_a = set(details_a.keywords)
        set_b = set(details_b.keywords)
        common_keywords = sorted(set_a.intersection(set_b))

        # Identify differences
        differences: list[str] = []

        if details_a.title != details_b.title:
            differences.append("Tytuły różnią się")

        if (details_a.type or "N/A") != (details_b.type or "N/A"):
            differences.append(
                f"Typy różnią się: '{details_a.type or 'N/A'}' vs '{details_b.type or 'N/A'}'"
            )

        if details_a.status != details_b.status:
            differences.append(
                f"Statusy różnią się: '{details_a.status}' vs '{details_b.status}'"
            )

        if details_a.promulgation_date != details_b.promulgation_date:
            differences.append(
                f"Daty promulgacji różnią się: "
                f"'{details_a.promulgation_date or 'N/A'}' vs "
                f"'{details_b.promulgation_date or 'N/A'}'"
            )

        if details_a.entry_into_force != details_b.entry_into_force:
            differences.append(
                f"Daty wejścia w życie różnią się: "
                f"'{details_a.entry_into_force or 'N/A'}' vs "
                f"'{details_b.entry_into_force or 'N/A'}'"
            )

        if set_a != set_b:
            only_a = set_a - set_b
            only_b = set_b - set_a
            if only_a:
                differences.append(
                    f"Słowa kluczowe tylko w A: {', '.join(sorted(only_a))}"
                )
            if only_b:
                differences.append(
                    f"Słowa kluczowe tylko w B: {', '.join(sorted(only_b))}"
                )

        if not differences:
            differences.append("Brak istotnych różnic w metadanych")

        output = CompareOutput(
            eli_a=eli_a,
            eli_b=eli_b,
            comparison=comparison,
            common_keywords=common_keywords,
            differences=differences,
        )

        response = EnrichedResponse(
            data=output,
            hints=compare_hints(eli_a, eli_b),
        )

        return response.model_dump_json()
