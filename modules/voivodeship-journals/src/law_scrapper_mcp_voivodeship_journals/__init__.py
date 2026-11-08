"""Source module for the voivodeship official journals (e-Dziennik ELI).

It adds no tool. It teaches the existing tools to serve the publisher codes `POL_WOJ_*`: the start hook
creates one egress client per served journal host and registers an `ActSource` that the core
services consult by publisher code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from law_scrapper_mcp.client.egress import EgressHost
from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModule
from law_scrapper_mcp_voivodeship_journals.catalog import CODE_PREFIX, JOURNALS, PUBLISHER_INFO, SERVED
from law_scrapper_mcp_voivodeship_journals.coverage import JournalCoverageProvider
from law_scrapper_mcp_voivodeship_journals.instructions import build_instructions
from law_scrapper_mcp_voivodeship_journals.source import JournalActSource
from law_scrapper_mcp_voivodeship_journals.state import MODULE_NAME, JournalsState

__all__ = ["module"]


def _register_no_tools(_server: object) -> None:
    """The module adds no tool; there is nothing to register."""


@asynccontextmanager
async def start(host: ModuleHost) -> AsyncIterator[JournalsState]:
    """Register one egress client per served journal and the act source for all `POL_WOJ_*` codes.

    Clients are created here, not at import, so each host appears on `/health` under its publisher
    code and its origin is reserved. No request is sent at start. The source and the coverage
    providers are unregistered when the hook exits, before the core closes its resources.
    """
    clients = {
        journal.code: host.egress.client_for(EgressHost(name=journal.code, base_url=journal.base_url), host.caller)
        for journal in SERVED
    }
    source = JournalActSource(clients, JOURNALS, host.settings)
    host.act_sources.register(
        source,
        publishers=[journal.code for journal in JOURNALS],
        prefixes=(CODE_PREFIX,),
        info=PUBLISHER_INFO,
    )
    registered: list[str] = []
    try:
        # One provider per served journal, on that journal's own client; a withheld journal has none.
        for journal in SERVED:
            provider = JournalCoverageProvider(journal, clients[journal.code])
            host.coverage.register(provider)
            registered.append(provider.key)
        yield JournalsState(codes=tuple(clients))
    finally:
        for key in registered:
            host.coverage.unregister(key)
        host.act_sources.unregister(source)


module = SourceModule(
    name=MODULE_NAME,
    tool_names=(),
    instructions=build_instructions(),
    register_tools=_register_no_tools,
    start=start,
)
