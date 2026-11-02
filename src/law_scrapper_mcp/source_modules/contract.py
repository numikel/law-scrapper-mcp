"""The contract between the core and a source module."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from mcp.server import MCPServer

from law_scrapper_mcp.client.egress import CallerIdentity, EgressHub
from law_scrapper_mcp.client.exceptions import EgressConfigError
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.context import AppContext

ENTRY_POINT_GROUP = "law_scrapper_mcp.source_modules"


class SourceModuleError(Exception):
    """A source module cannot be loaded, started or reconciled with the core.

    Messages are Polish and name the module and, where it helps, the `LAW_MCP_SOURCE_MODULES`
    token that switches it off: the server does not start, so the operator reads this first.
    """


@dataclass(frozen=True, slots=True)
class ModuleHost:
    """What the core hands to a module's start hook.

    A module registers its upstream host by calling `egress.client_for(EgressHost(...), caller)`
    inside the start hook; that call is what puts the host on `/health` and reserves its origin.
    A module that never calls it has no host.

    Attributes:
        egress: The registry of upstream hosts, the same object as `AppContext.egress`.
        caller: Identity derived by the core from the module's distribution, never filled in by
            the module, so a module cannot forget or spoof its attribution.
        settings: The process settings.
    """

    egress: EgressHub
    caller: CallerIdentity
    settings: Settings


@dataclass(frozen=True, slots=True)
class SourceModule:
    """Static description of a module, available before the server exists.

    Attributes:
        name: Module name; equal to its entry-point name and shaped `[a-z][a-z0-9-]*`.
        tool_names: Every tool `register_tools` adds. The core verifies the set of names that
            appears after the call equals this tuple and that none existed before.
        instructions: Polish block appended to the server instructions when the module is active.
        register_tools: Registers the module's tools on the server; called once, at build time.
        start: Optional async context manager factory run inside the server lifespan, after the
            core resources exist and closed before they are. The value it yields is stored in
            `AppContext.module_state[name]`.
    """

    name: str
    tool_names: tuple[str, ...]
    instructions: str
    register_tools: Callable[[MCPServer[AppContext]], None]
    start: Callable[[ModuleHost], AbstractAsyncContextManager[object]] | None = None


@dataclass(frozen=True, slots=True)
class LoadedModule:
    """A descriptor together with the distribution it came from."""

    module: SourceModule
    distribution: str
    version: str

    def caller_identity(self) -> CallerIdentity:
        """Derive the egress identity from the distribution, naming the module on failure."""
        try:
            return CallerIdentity(module=self.distribution, version=self.version)
        except EgressConfigError as error:
            raise SourceModuleError(
                f"Moduł {self.module.name}: nazwa lub wersja pakietu ({self.distribution} {self.version}) "
                f"nie jest znacznikiem RFC 9110. Wyłącz moduł: LAW_MCP_SOURCE_MODULES=-{self.module.name}."
            ) from error
