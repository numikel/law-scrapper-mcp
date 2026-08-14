"""Domain service for comparing legal acts."""

import asyncio
from typing import Any

from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, CompareOutput
from law_scrapper_mcp.services.act_service import ActService


class ComparisonService:
    """Compare metadata for two legal acts."""

    def __init__(self, act_service: ActService) -> None:
        self._act_service = act_service

    async def _fetch_details(self, eli_a: str, eli_b: str) -> tuple[ActDetailOutput, ActDetailOutput]:
        if eli_a == eli_b:
            details = await self._act_service.get_details(eli=eli_a, load_content=False)
            return details, details

        task_a = asyncio.create_task(self._act_service.get_details(eli=eli_a, load_content=False))
        task_b = asyncio.create_task(self._act_service.get_details(eli=eli_b, load_content=False))
        tasks = (task_a, task_b)
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

            for task in done:
                if exc := task.exception():
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise exc

            return task_a.result(), task_b.result()
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def compare(self, eli_a: str, eli_b: str) -> CompareOutput:
        details_a, details_b = await self._fetch_details(eli_a, eli_b)
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
        keywords_a = set(details_a.keywords)
        keywords_b = set(details_b.keywords)
        differences: list[str] = []
        if details_a.title != details_b.title:
            differences.append("Tytuły różnią się")
        if (details_a.type or "N/A") != (details_b.type or "N/A"):
            differences.append(f"Typy różnią się: '{details_a.type or 'N/A'}' vs '{details_b.type or 'N/A'}'")
        if details_a.status != details_b.status:
            differences.append(f"Statusy różnią się: '{details_a.status}' vs '{details_b.status}'")
        if details_a.promulgation_date != details_b.promulgation_date:
            differences.append(
                "Daty promulgacji różnią się: "
                f"'{details_a.promulgation_date or 'N/A'}' vs '{details_b.promulgation_date or 'N/A'}'"
            )
        if details_a.entry_into_force != details_b.entry_into_force:
            differences.append(
                "Daty wejścia w życie różnią się: "
                f"'{details_a.entry_into_force or 'N/A'}' vs '{details_b.entry_into_force or 'N/A'}'"
            )
        only_a = sorted(keywords_a - keywords_b)
        only_b = sorted(keywords_b - keywords_a)
        if only_a:
            differences.append(f"Słowa kluczowe tylko w A: {', '.join(only_a)}")
        if only_b:
            differences.append(f"Słowa kluczowe tylko w B: {', '.join(only_b)}")
        if not differences:
            differences.append("Brak istotnych różnic w metadanych")
        return CompareOutput(
            eli_a=eli_a,
            eli_b=eli_b,
            comparison=comparison,
            common_keywords=sorted(keywords_a & keywords_b),
            differences=differences,
        )
