"""Fake source modules and entry points for the module-mechanism unit tests.

Nothing here is installed: descriptors are built in memory and entry points are plain
`InstalledEntryPoint` records whose `load` is a spy, so each test states exactly which modules
exist and can assert which of them were imported.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from mcp.server import MCPServer

from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.source_modules.contract import LoadedModule, ModuleHost, SourceModule
from law_scrapper_mcp.source_modules.discovery import InstalledEntryPoint


def register_named_tools(*tool_names: str) -> Callable[[MCPServer[AppContext]], None]:
    """A `register_tools` that adds one trivial tool per name."""

    def register(server: MCPServer[AppContext]) -> None:
        for tool_name in tool_names:

            async def tool() -> str:
                return "ok"

            server.add_tool(tool, name=tool_name)

    return register


def fake_module(
    name: str = "alpha",
    tools: Sequence[str] | None = None,
    *,
    instructions: str | None = None,
    start: Callable[[ModuleHost], AbstractAsyncContextManager[object]] | None = None,
    register: Callable[[MCPServer[AppContext]], None] | None = None,
) -> SourceModule:
    """A descriptor that registers `tools` (default: one tool named after the module)."""
    tool_names = tuple(tools) if tools is not None else (f"{name.replace('-', '_')}_ping",)
    return SourceModule(
        name=name,
        tool_names=tool_names,
        instructions=instructions if instructions is not None else f"Moduł {name}: testowy blok instrukcji.",
        register_tools=register if register is not None else register_named_tools(*tool_names),
        start=start,
    )


def loaded(
    module: SourceModule | None = None,
    *,
    distribution: str | None = None,
    version: str = "1.0.0",
) -> LoadedModule:
    module = module or fake_module()
    return LoadedModule(module=module, distribution=distribution or f"law-scrapper-mcp-{module.name}", version=version)


@dataclass
class SpyEntryPoint:
    """An entry point that records whether anything imported it."""

    name: str
    target: object
    distribution: str = ""
    version: str = "1.0.0"
    loads: list[str] = field(default_factory=list)

    @property
    def entry_point(self) -> InstalledEntryPoint:
        def load() -> object:
            self.loads.append(self.name)
            if isinstance(self.target, BaseException):
                raise self.target
            return self.target

        return InstalledEntryPoint(
            name=self.name,
            distribution=self.distribution or f"law-scrapper-mcp-{self.name}",
            version=self.version,
            load=load,
        )


def spy_entry_points(*names: str) -> dict[str, SpyEntryPoint]:
    """One spy per name, each pointing at a valid fake descriptor of the same name."""
    return {name: SpyEntryPoint(name, fake_module(name)) for name in names}


def started(
    state: object, events: list[str] | None = None, label: str = ""
) -> Callable[[ModuleHost], AbstractAsyncContextManager[object]]:
    """A start hook that yields `state` and records `start:<label>` / `stop:<label>` in `events`."""

    @asynccontextmanager
    async def hook(_host: ModuleHost) -> AsyncIterator[object]:
        if events is not None:
            events.append(f"start:{label}")
        try:
            yield state
        finally:
            if events is not None:
                events.append(f"stop:{label}")

    return hook
