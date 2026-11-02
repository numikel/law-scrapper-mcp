"""Find installed source modules, decide which are active and load only those."""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass

from mcp.server import MCPServer

from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.source_modules.contract import (
    ENTRY_POINT_GROUP,
    LoadedModule,
    SourceModule,
    SourceModuleError,
)

_MODULE_NAME = re.compile(r"[a-z][a-z0-9-]*")


@dataclass(frozen=True, slots=True)
class InstalledEntryPoint:
    """One entry point of the module group, with the distribution it was installed by.

    The seam tests use: `discover` takes these instead of `importlib.metadata.EntryPoint`,
    because a hand-built `EntryPoint` has no distribution to read a name and version from.
    """

    name: str
    distribution: str
    version: str
    load: Callable[[], object]


@dataclass(frozen=True, slots=True)
class Activation:
    """Outcome of evaluating `LAW_MCP_SOURCE_MODULES` against the installed module names.

    Attributes:
        active: Installed modules that run, sorted by name.
        inactive: Installed modules that do not run, sorted by name.
        unknown_enabled: Names that are not installed and still enabled after the whole pass.
            A startup error.
        unknown_ignored: Names that are not installed and ended up disabled (a `-name` token or an
            enable that a later token switched off). Accepted, and reported in a WARNING.
    """

    active: tuple[str, ...]
    inactive: tuple[str, ...]
    unknown_enabled: tuple[str, ...]
    unknown_ignored: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Discovery:
    """What `discover` found, in the form `build_app` and the startup log need.

    Attributes:
        active: Loaded, validated modules, sorted by name.
        inactive: Installed module names that are switched off; never imported.
        requested: The validated tokens of `LAW_MCP_SOURCE_MODULES`, as supplied.
        ignored_unknown: Unknown names that ended up disabled (see `Activation`).
    """

    active: tuple[LoadedModule, ...] = ()
    inactive: tuple[str, ...] = ()
    requested: tuple[str, ...] = ()
    ignored_unknown: tuple[str, ...] = ()


def resolve_activation(tokens: Sequence[str], installed: Collection[str]) -> Activation:
    """Apply the tokens from left to right; the last token about a module wins.

    Every installed module starts enabled. `name` enables a module, `-name` disables it, `*`
    enables every installed module and `-*` disables every name, installed or not. A name that is
    not installed is an error only if it is still enabled at the end, so `foo,-foo` and `-foo` are
    accepted (and reported), while `foo` and `-*,foo` are not.

    The tokens must already be syntactically valid (`Settings.source_modules` guarantees it).
    """
    known = frozenset(installed)
    enabled: set[str] = set(known)
    mentioned_unknown: set[str] = set()
    for token in tokens:
        if token == "-*":
            enabled.clear()
        elif token == "*":
            enabled |= known
        else:
            name = token.removeprefix("-")
            if token.startswith("-"):
                enabled.discard(name)
            else:
                enabled.add(name)
            if name not in known:
                mentioned_unknown.add(name)
    unknown_enabled = enabled - known
    return Activation(
        active=tuple(sorted(enabled & known)),
        inactive=tuple(sorted(known - enabled)),
        unknown_enabled=tuple(sorted(unknown_enabled)),
        unknown_ignored=tuple(sorted(mentioned_unknown - unknown_enabled)),
    )


def installed_entry_points() -> tuple[InstalledEntryPoint, ...]:
    """Read the entry points of the module group from the current environment."""
    found: list[InstalledEntryPoint] = []
    for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        dist = entry_point.dist
        if dist is None:
            raise SourceModuleError(
                f"Moduł {entry_point.name}: nie da się ustalić pakietu, który go zainstalował. "
                f"Wyłącz moduł: LAW_MCP_SOURCE_MODULES=-{entry_point.name}."
            )
        found.append(
            InstalledEntryPoint(
                name=entry_point.name,
                distribution=dist.name,
                version=dist.version,
                load=entry_point.load,
            )
        )
    return tuple(found)


def _disable_hint(name: str) -> str:
    return f"Wyłącz moduł, dopisując -{name} na końcu LAW_MCP_SOURCE_MODULES."


def _load(candidate: InstalledEntryPoint) -> LoadedModule:
    name = candidate.name
    try:
        loaded = candidate.load()
    except Exception as error:
        raise SourceModuleError(
            f"Moduł {name} ({candidate.distribution}) nie załadował się: {type(error).__name__}: {error}. "
            f"{_disable_hint(name)}"
        ) from error
    if not isinstance(loaded, SourceModule):
        raise SourceModuleError(
            f"Moduł {name} ({candidate.distribution}): punkt wejścia nie wskazuje obiektu SourceModule "
            f"(jest {type(loaded).__name__}). {_disable_hint(name)}"
        )
    if loaded.name != name:
        raise SourceModuleError(
            f"Moduł {name} ({candidate.distribution}): deskryptor nazywa się {loaded.name}, a punkt wejścia {name}. "
            f"{_disable_hint(name)}"
        )
    if len(set(loaded.tool_names)) != len(loaded.tool_names):
        raise SourceModuleError(f"Moduł {name}: lista tool_names powtarza nazwę narzędzia. {_disable_hint(name)}")
    return LoadedModule(module=loaded, distribution=candidate.distribution, version=candidate.version)


def discover(settings: Settings, entry_points: Iterable[InstalledEntryPoint] | None = None) -> Discovery:
    """Decide which installed modules run, then load and validate exactly those.

    A module that is switched off is never imported, so a broken module stops being a problem the
    moment the operator disables it. Everything else that is wrong stops startup with a Polish
    message naming the module and the remedy.
    """
    candidates = tuple(installed_entry_points() if entry_points is None else entry_points)
    # Before anything is switched on or imported: a name the variable cannot spell has no
    # `-name` remedy, so the only way out is to remove or fix the package, and its import-time
    # code must not run on the way to that message.
    for candidate in candidates:
        if not _MODULE_NAME.fullmatch(candidate.name):
            raise SourceModuleError(
                f"Pakiet {candidate.distribution} rejestruje moduł o niedozwolonej nazwie {candidate.name!r}: "
                "nazwa musi składać się z małych liter, cyfr i myślników i zaczynać się od litery. "
                "Zmiennej LAW_MCP_SOURCE_MODULES nie da się go wyłączyć; odinstaluj pakiet albo popraw nazwę "
                "punktu wejścia."
            )
    names = [candidate.name for candidate in candidates]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise SourceModuleError(
            f"Dwa pakiety rejestrują moduł o tej samej nazwie: {', '.join(duplicated)}. Odinstaluj jeden z nich."
        )

    tokens = tuple(settings.source_modules)
    activation = resolve_activation(tokens, names)
    if activation.unknown_enabled:
        installed = ", ".join(sorted(names)) or "brak"
        raise SourceModuleError(
            f"LAW_MCP_SOURCE_MODULES włącza moduł, którego nie ma: {', '.join(activation.unknown_enabled)}. "
            f"Zainstalowane moduły: {installed}."
        )

    by_name = {candidate.name: candidate for candidate in candidates}
    loaded = tuple(_load(by_name[name]) for name in activation.active)

    owner: dict[str, str] = {}
    for item in loaded:
        for tool_name in item.module.tool_names:
            if tool_name in owner:
                raise SourceModuleError(
                    f"Moduły {owner[tool_name]} i {item.module.name} deklarują to samo narzędzie: {tool_name}. "
                    f"{_disable_hint(item.module.name)}"
                )
            owner[tool_name] = item.module.name

    return Discovery(
        active=loaded,
        inactive=activation.inactive,
        requested=tokens,
        ignored_unknown=activation.unknown_ignored,
    )


def registered_tool_names(server: MCPServer[AppContext]) -> frozenset[str]:
    """Names of the tools currently registered on `server`.

    The only place that touches an SDK-private attribute: the SDK ignores a duplicate tool name
    with a warning, so the core has to compare the registered set itself to detect a clash. Re-check
    this on every `mcp` upgrade; `tests/unit/test_source_module_discovery.py` fails if the
    attribute moves.
    """
    manager = getattr(server, "_tool_manager", None)
    list_tools = getattr(manager, "list_tools", None)
    if list_tools is None:
        raise RuntimeError(
            "The installed mcp SDK no longer exposes MCPServer._tool_manager.list_tools(); "
            "source_modules.discovery.registered_tool_names must be updated for the new SDK."
        )
    return frozenset(tool.name for tool in list_tools())
