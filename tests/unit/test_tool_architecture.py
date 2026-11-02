"""Architecture guards for thin MCP adapters.

The previous version of this guard matched substrings, so it only constrained
*how* a handler reached the lifespan context. It passed for handlers that
looped over domain data and formatted user-facing text. These checks parse the
adapters instead and constrain *what* a handler is allowed to do.

Known limit: model-mapping comprehensions (`[Model.model_validate(d) for d in
raw]`) are still allowed, because turning a store's dict into its output model
is adapter work, not domain work.

The same parse enforces the decorator convention from CLAUDE.md: every
`@mcp.tool(...)` passes a non-empty literal `title=` and an `annotations=` preset
imported from `tools/annotations.py`, so a future write tool cannot inherit
read-only hints without a deliberate choice.

The layer guard parses every module under `models/`, `client/`, `services/` and
`tools/` and checks that imports point only towards the layers beneath:
`tools/` reaches `services/` and `models/` but not `client/`, and `httpx` stays
in `client/`. Relative imports are resolved, so rewriting an import as
`from ..client import x` does not slip past it. Root modules (`config.py`,
`context.py`, `logging_config.py`) belong to no layer and stay open to all.
"""

import ast
from pathlib import Path

import pytest

from mcp_helpers import EXPECTED_TOOLS

REPO_ROOT = Path(__file__).parents[2]
TOOLS_ROOT = REPO_ROOT / "src" / "law_scrapper_mcp" / "tools"
# Source modules mirror the core layout: modules/<source>/src/law_scrapper_mcp_<source>/tools/.
MODULE_TOOL_ROOTS = sorted((REPO_ROOT / "modules").glob("*/src/law_scrapper_mcp_*/tools"))
SKIPPED_MODULES = {"__init__.py", "error_handling.py"}
MAX_AWAITS_PER_HANDLER = 1
HINT_FACTORIES = {"Hint"}
PRESET_MODULE = "law_scrapper_mcp.tools.annotations"
PRESET_SOURCE = TOOLS_ROOT / "annotations.py"
EXPECTED_TOOL_COUNT = len(EXPECTED_TOOLS)

# Messages and import line used by the guard-the-guard cases below.
ANNOTATIONS_PROBLEM = f"tool: annotations= must name a preset imported from {PRESET_MODULE}"
TITLE_PROBLEM = "tool: title= must be a non-empty string literal"
IMPORT_LOCAL = f"from {PRESET_MODULE} import READ_ONLY_LOCAL"

PACKAGE = "law_scrapper_mcp"
SRC_ROOT = TOOLS_ROOT.parent
HTTP_LIBRARY = "httpx"
# What each layer may import; `httpx` counts as a layer of its own.
ALLOWED_LAYER_IMPORTS = {
    "models": frozenset({"models"}),
    "client": frozenset({"models", "client", HTTP_LIBRARY}),
    "services": frozenset({"models", "client", "services"}),
    "tools": frozenset({"models", "services", "tools"}),
}
# error_handling.py maps client and httpx exceptions onto tool errors, so it has to name them.
LAYER_EXCEPTIONS = {"tools/error_handling.py": frozenset({"client", HTTP_LIBRARY})}

Handler = ast.FunctionDef | ast.AsyncFunctionDef


def _core_tool_modules() -> list[Path]:
    return sorted(path for path in TOOLS_ROOT.glob("*.py") if path.name not in SKIPPED_MODULES)


def _module_tool_modules() -> list[Path]:
    return sorted(path for root in MODULE_TOOL_ROOTS for path in root.glob("*.py") if path.name not in SKIPPED_MODULES)


def _tool_modules() -> list[Path]:
    """Every file that may hold tool handlers: the core's and those of every source module."""
    return [*_core_tool_modules(), *_module_tool_modules()]


def _tool_decorator(node: Handler) -> ast.Call | None:
    """Return the `@mcp.tool(...)` call decorating the function, or None."""
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
        ):
            return decorator
    return None


def _handlers(tree: ast.Module) -> list[Handler]:
    """Return every `@mcp.tool(...)`-decorated function in the module, sync or async."""
    return [node for node in ast.walk(tree) if isinstance(node, Handler) and _tool_decorator(node) is not None]


def _is_hint_factory(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name in HINT_FACTORIES or name.endswith("_hints")


def _body_nodes(handler: Handler) -> list[ast.AST]:
    """Walk the handler body only.

    The signature is excluded on purpose: `Field(description=f"...")` on a tool
    parameter is protocol documentation, not response text.
    """
    nodes: list[ast.AST] = []
    for statement in handler.body:
        nodes.extend(ast.walk(statement))
    return nodes


def _formatted_strings_outside_hints(handler: Handler) -> list[int]:
    """Line numbers of f-strings that are not part of building a hint."""
    body = _body_nodes(handler)
    allowed: set[int] = set()
    for node in body:
        if _is_hint_factory(node):
            allowed.update(id(inner) for inner in ast.walk(node) if isinstance(inner, ast.JoinedStr))
    return [node.lineno for node in body if isinstance(node, ast.JoinedStr) and id(node) not in allowed]


def _tool_decorators(tree: ast.Module) -> list[tuple[str, ast.Call]]:
    """Return (handler name, `@mcp.tool(...)` call) for every tool in the module."""
    return [(handler.name, call) for handler in _handlers(tree) if (call := _tool_decorator(handler)) is not None]


def _preset_names(source: str) -> frozenset[str]:
    """Names bound at module level to a `ToolAnnotations(...)` call: the presets a tool may name.

    Both `NAME = ToolAnnotations(...)` and the annotated
    `NAME: Final[ToolAnnotations] = ToolAnnotations(...)` count; the shipped presets use the latter.
    """
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "ToolAnnotations":
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return frozenset(names)


PRESETS = _preset_names(PRESET_SOURCE.read_text(encoding="utf-8"))


def _imported_presets(tree: ast.Module, known: frozenset[str] = PRESETS) -> set[str]:
    """Every expression that names a known preset in this module, in `ast.unparse` spelling.

    `from <PRESET_MODULE> import READ_ONLY_LOCAL as RO` yields the local name `RO`.
    Importing the module itself (`import <PRESET_MODULE> [as ann]` or
    `from law_scrapper_mcp.tools import annotations [as ann]`) yields
    `<prefix>.<preset>` for every known preset. Relative imports are not
    recognised; the tool modules import absolutely.
    """
    package, _, module = PRESET_MODULE.rpartition(".")
    references: set[str] = set()
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == PRESET_MODULE and alias.name in known:
                    references.add(alias.asname or alias.name)
                elif node.module == package and alias.name == module:
                    prefixes.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            prefixes.update(alias.asname or alias.name for alias in node.names if alias.name == PRESET_MODULE)
    return references | {f"{prefix}.{preset}" for prefix in prefixes for preset in known}


def _decorator_problems(tree: ast.Module, known: frozenset[str] = PRESETS) -> list[str]:
    """A tool must pick a shared preset on purpose and carry a human title."""
    presets = _imported_presets(tree, known)
    problems: list[str] = []
    for name, decorator in _tool_decorators(tree):
        keywords = {keyword.arg: keyword.value for keyword in decorator.keywords}
        annotations = keywords.get("annotations")
        if not (isinstance(annotations, ast.Name | ast.Attribute) and ast.unparse(annotations) in presets):
            problems.append(f"{name}: annotations= must name a preset imported from {PRESET_MODULE}")
        title = keywords.get("title")
        if not (isinstance(title, ast.Constant) and isinstance(title.value, str) and title.value.strip()):
            problems.append(f"{name}: title= must be a non-empty string literal")
    return problems


def _tool_module(imports: str, arguments: str, *, sync: bool = False) -> ast.Module:
    """Parse a module with the given import lines and one `@mcp.tool(<arguments>)` handler named `tool`."""
    return ast.parse(
        "\n".join(
            [
                imports,
                "def register(mcp):",
                f"    @mcp.tool({arguments})",
                f"    {'def' if sync else 'async def'} tool(ctx):",
                "        return None",
            ]
        )
    )


def _imported_modules(tree: ast.Module, package: str) -> set[str]:
    """Absolute names of everything the module imports, relative imports resolved against `package`.

    `from X import Y` yields both `X` and `X.Y`, because `Y` may itself be a
    subpackage: `from law_scrapper_mcp import client` names the layer only in `Y`.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                anchor = ".".join(parts[: len(parts) - node.level + 1])
                base = f"{anchor}.{node.module}" if node.module else anchor
            else:
                base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _import_target(name: str) -> str | None:
    """The layer an imported name belongs to, `httpx` for the HTTP library, or None when unrestricted."""
    parts = name.split(".")
    if parts[0] == HTTP_LIBRARY:
        return HTTP_LIBRARY
    if parts[0] == PACKAGE and len(parts) > 1 and parts[1] in ALLOWED_LAYER_IMPORTS:
        return parts[1]
    return None


def _layer_problems(tree: ast.Module, layer: str, filename: str) -> list[str]:
    """One message per layer the module imports but may not."""
    module = f"{layer}/{filename}"
    allowed = ALLOWED_LAYER_IMPORTS[layer] | LAYER_EXCEPTIONS.get(module, frozenset())
    targets = {_import_target(name) for name in _imported_modules(tree, f"{PACKAGE}.{layer}")}
    return [f"{module} must not import {target}" for target in sorted(t for t in targets if t and t not in allowed)]


def test_layers_import_only_the_layers_beneath_them() -> None:
    """`models/` → `client/` → `services/` → `tools/`: an import pointing right inverts the layering."""
    problems: list[str] = []
    checked = 0
    for layer in ALLOWED_LAYER_IMPORTS:
        for path in sorted((SRC_ROOT / layer).glob("*.py")):
            checked += 1
            problems.extend(_layer_problems(ast.parse(path.read_text(encoding="utf-8")), layer, path.name))
    assert checked > EXPECTED_TOOL_COUNT, "the layer guard found too few modules to have scanned the package"
    assert problems == []


@pytest.mark.parametrize(
    ("layer", "filename", "source", "expected"),
    [
        pytest.param(
            "tools",
            "search.py",
            "from law_scrapper_mcp.client.sejm_client import SejmApiClient",
            ["tools/search.py must not import client"],
            id="tool-skips-the-service-layer",
        ),
        pytest.param("tools", "search.py", "import httpx", ["tools/search.py must not import httpx"], id="tool-httpx"),
        pytest.param(
            "services",
            "act_service.py",
            "from law_scrapper_mcp.tools.annotations import READ_ONLY_LOCAL",
            ["services/act_service.py must not import tools"],
            id="service-imports-a-tool",
        ),
        pytest.param(
            "client",
            "cache.py",
            "from ..services import act_service",
            ["client/cache.py must not import services"],
            id="relative-import-is-resolved",
        ),
        pytest.param(
            "models",
            "enums.py",
            "from law_scrapper_mcp import client",
            ["models/enums.py must not import client"],
            id="layer-named-in-the-alias",
        ),
        pytest.param(
            "services",
            "search_service.py",
            "import httpx\nfrom law_scrapper_mcp.tools import search",
            ["services/search_service.py must not import httpx", "services/search_service.py must not import tools"],
            id="every-offending-layer-reported",
        ),
    ],
)
def test_the_layer_guard_can_actually_fail(layer: str, filename: str, source: str, expected: list[str]) -> None:
    """Guard the guard: every way a module can point an import the wrong way, checked by message."""
    assert _layer_problems(ast.parse(source), layer, filename) == expected


@pytest.mark.parametrize(
    ("layer", "filename", "source"),
    [
        pytest.param(
            "tools",
            "error_handling.py",
            "import httpx\nfrom law_scrapper_mcp.client.exceptions import SejmAPIError",
            id="error-handling-exception",
        ),
        pytest.param(
            "tools",
            "search.py",
            "\n".join(
                [
                    "from law_scrapper_mcp.config import settings",
                    "from law_scrapper_mcp.context import get_app_context",
                    "from law_scrapper_mcp.models.tool_outputs import EnrichedResponse",
                    "from law_scrapper_mcp.services.pagination import paginate",
                    "from .annotations import READ_ONLY_REMOTE",
                ]
            ),
            id="tool-with-allowed-imports",
        ),
        pytest.param("client", "sejm_client.py", "import httpx\nfrom .cache import TTLCache", id="client-owns-httpx"),
    ],
)
def test_the_layer_guard_allows_imports_that_point_down(layer: str, filename: str, source: str) -> None:
    """Root modules, lower layers and the documented exception must not trip the guard."""
    assert _layer_problems(ast.parse(source), layer, filename) == []


def test_every_tool_module_reaches_the_context_only_through_the_typed_accessor() -> None:
    for path in _tool_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        leaked = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "lifespan_context"
        ]
        assert leaked == [], f"{path.name} touches lifespan_context directly at lines {leaked}"

        calls = {
            node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if _handlers(tree):
            assert "get_app_context" in calls, f"{path.name} does not use get_app_context"


def test_handlers_delegate_to_exactly_one_awaited_call() -> None:
    """A handler that awaits twice is orchestrating, which belongs in a service."""
    for path in _tool_modules():
        for handler in _handlers(ast.parse(path.read_text(encoding="utf-8"))):
            awaits = sum(isinstance(node, ast.Await) for node in _body_nodes(handler))
            assert awaits <= MAX_AWAITS_PER_HANDLER, (
                f"{path.name}::{handler.name} awaits {awaits} times; move the orchestration into a service"
            )


def test_handlers_do_not_format_user_facing_text() -> None:
    """Response text is domain output; only hints may interpolate in an adapter."""
    for path in _tool_modules():
        for handler in _handlers(ast.parse(path.read_text(encoding="utf-8"))):
            offending = _formatted_strings_outside_hints(handler)
            assert offending == [], (
                f"{path.name}::{handler.name} builds user-facing text at lines {offending}; "
                "move it into the service that owns the output model"
            )


def test_the_guard_can_actually_fail() -> None:
    """Guard the guard: these checks must reject a fat adapter."""
    fat_adapter = ast.parse(
        "\n".join(
            [
                "def register(mcp):",
                "    @mcp.tool(meta={'tags': []})",
                "    async def bad(ctx):",
                "        store = get_app_context(ctx).document_store",
                "        toc = await store.get_toc('DU/2024/1')",
                "        section = await store.get_section('DU/2024/1', 'Art. 1')",
                "        return f'Znaleziono {len(toc)} sekcji {section}'",
            ]
        )
    )
    handlers = _handlers(fat_adapter)

    assert len(handlers) == 1
    assert sum(isinstance(node, ast.Await) for node in _body_nodes(handlers[0])) > MAX_AWAITS_PER_HANDLER
    assert _formatted_strings_outside_hints(handlers[0]) != []

    # A synchronous handler must not escape the static rules.
    sync_adapter = ast.parse(
        "\n".join(
            [
                "def register(mcp):",
                "    @mcp.tool(meta={'tags': []})",
                "    def bad(ctx):",
                "        return f'Znaleziono {ctx}'",
            ]
        )
    )
    sync_handlers = _handlers(sync_adapter)

    assert [handler.name for handler in sync_handlers] == ["bad"]
    assert _formatted_strings_outside_hints(sync_handlers[0]) != []


def test_every_tool_declares_an_annotation_preset_and_a_title() -> None:
    """A future write tool cannot inherit read-only hints without a deliberate choice."""
    core_total = 0
    for path in _tool_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.parent == TOOLS_ROOT:
            core_total += len(_tool_decorators(tree))
        problems = _decorator_problems(tree)
        assert problems == [], f"{path.name}: {problems}"
    # The count is the core's alone: a module adds its own tools on top of the 13.
    assert core_total == EXPECTED_TOOL_COUNT


@pytest.mark.parametrize(
    ("imports", "arguments", "expected"),
    [
        pytest.param("", "meta={'tags': []}", [ANNOTATIONS_PROBLEM, TITLE_PROBLEM], id="bare-decorator"),
        pytest.param(
            "from mcp.types import ToolAnnotations",
            "title='Zły', annotations=ToolAnnotations(read_only_hint=True)",
            [ANNOTATIONS_PROBLEM],
            id="inline-tool-annotations",
        ),
        pytest.param(IMPORT_LOCAL, "title='Zły', annotations=CUSTOM", [ANNOTATIONS_PROBLEM], id="not-a-preset"),
        pytest.param(
            f"{IMPORT_LOCAL} as RO",
            "title='Zły', annotations=READ_ONLY_LOCAL",
            [ANNOTATIONS_PROBLEM],
            id="alias-hides-the-original-name",
        ),
        pytest.param(
            "import os", "title='Zły', annotations=os.READ_ONLY_LOCAL", [ANNOTATIONS_PROBLEM], id="another-module"
        ),
        pytest.param(
            f"import {PRESET_MODULE} as ann",
            "title='Zły', annotations=ann.CUSTOM",
            [ANNOTATIONS_PROBLEM],
            id="not-a-preset-of-the-module",
        ),
        pytest.param(IMPORT_LOCAL, "title='', annotations=READ_ONLY_LOCAL", [TITLE_PROBLEM], id="empty-title"),
        pytest.param(IMPORT_LOCAL, "title='   ', annotations=READ_ONLY_LOCAL", [TITLE_PROBLEM], id="blank-title"),
        pytest.param(IMPORT_LOCAL, "title=TITLE, annotations=READ_ONLY_LOCAL", [TITLE_PROBLEM], id="name-title"),
        pytest.param(
            IMPORT_LOCAL, "title=f'Zły {x}', annotations=READ_ONLY_LOCAL", [TITLE_PROBLEM], id="f-string-title"
        ),
        pytest.param(IMPORT_LOCAL, "title=1, annotations=READ_ONLY_LOCAL", [TITLE_PROBLEM], id="non-string-title"),
    ],
)
def test_the_annotation_guard_can_actually_fail(imports: str, arguments: str, expected: list[str]) -> None:
    """Guard the guard: every way a decorator can be rejected, checked by message, not by count."""
    assert _decorator_problems(_tool_module(imports, arguments)) == expected


@pytest.mark.parametrize(
    ("imports", "annotations"),
    [
        pytest.param(IMPORT_LOCAL, "READ_ONLY_LOCAL", id="by-name"),
        pytest.param(f"{IMPORT_LOCAL} as RO", "RO", id="aliased-name"),
        pytest.param("from law_scrapper_mcp.tools import annotations", "annotations.READ_ONLY_REMOTE", id="package"),
        pytest.param(
            "from law_scrapper_mcp.tools import annotations as ann", "ann.READ_ONLY_LOCAL", id="aliased-package"
        ),
        pytest.param(f"import {PRESET_MODULE} as ann", "ann.READ_ONLY_REMOTE", id="aliased-module"),
        pytest.param(f"import {PRESET_MODULE}", f"{PRESET_MODULE}.READ_ONLY_LOCAL", id="dotted-module"),
    ],
)
def test_the_annotation_guard_honours_every_way_to_name_a_preset(imports: str, annotations: str) -> None:
    """An aliased or module-qualified preset is still a preset; rejecting it would be a false positive."""
    assert _decorator_problems(_tool_module(imports, f"title='Dobry', annotations={annotations}")) == []


def test_the_annotation_guard_covers_sync_handlers_and_named_presets() -> None:
    """A sync handler is checked like an async one, and a named preset counts exactly when tools/annotations.py defines it."""
    annotations_source = "\n".join(
        [
            "from typing import Final",
            "from mcp.types import ToolAnnotations",
            "READ_ONLY_LOCAL = ToolAnnotations(read_only_hint=True)",
            "READ_ONLY_REMOTE: Final[ToolAnnotations] = ToolAnnotations(read_only_hint=True)",
            "WRITE_REMOTE = ToolAnnotations(read_only_hint=False, destructive_hint=False)",
            "NOT_A_PRESET = 1",
            "ANNOTATED_ONLY: ToolAnnotations",
            "def helper():",
            "    NESTED = ToolAnnotations(read_only_hint=True)",
        ]
    )
    with_write_preset = _preset_names(annotations_source)
    write_tool = _tool_module(f"from {PRESET_MODULE} import WRITE_REMOTE", "title='Zapis', annotations=WRITE_REMOTE")

    assert _decorator_problems(_tool_module("", "meta={'tags': []}", sync=True)) == [ANNOTATIONS_PROBLEM, TITLE_PROBLEM]
    assert with_write_preset == {"READ_ONLY_LOCAL", "READ_ONLY_REMOTE", "WRITE_REMOTE"}
    assert _decorator_problems(write_tool, with_write_preset) == []
    assert _decorator_problems(write_tool) == [ANNOTATIONS_PROBLEM]


# --- source modules -------------------------------------------------------------------------------
#
# A module's tools mirror the core's: thin adapters with a title and a preset (the tests above now run
# over every module tool package as well). On top of that a module reaches upstream only through the
# host object the core hands its start hook, never through `httpx` or the core `client/` layer, and the
# core never imports a module.

MODULE_PACKAGE_PREFIX = f"{PACKAGE}_"
MODULE_FORBIDDEN_TARGETS = frozenset({"client", HTTP_LIBRARY})
DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _module_tool_import_problems(tree: ast.Module, filename: str) -> list[str]:
    """One message per forbidden layer a module's tool file imports."""
    targets = {_import_target(name) for name in _imported_modules(tree, f"{MODULE_PACKAGE_PREFIX}x.tools")}
    return [
        f"{filename} must not import {target}" for target in sorted(t for t in targets if t in MODULE_FORBIDDEN_TARGETS)
    ]


def _hard_wired_module_imports(tree: ast.Module) -> list[str]:
    """Names of source-module packages the file imports, statically or by a literal `import_module(...)`."""
    names = {name for name in _imported_modules(tree, PACKAGE) if name.split(".")[0].startswith(MODULE_PACKAGE_PREFIX)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        target = node.args[0].value
        if called in DYNAMIC_IMPORTERS and isinstance(target, str) and target.startswith(MODULE_PACKAGE_PREFIX):
            names.add(target)
    return sorted(names)


def test_the_module_tool_packages_were_found() -> None:
    """Without this, an empty glob would turn every module check above and below into a pass."""
    assert MODULE_TOOL_ROOTS, "no modules/<source>/src/law_scrapper_mcp_<source>/tools/ found"
    assert _module_tool_modules(), "the module tool packages hold no tool module"


def test_module_tools_do_not_reach_httpx_or_the_core_client_layer() -> None:
    problems: list[str] = []
    for root in MODULE_TOOL_ROOTS:
        for path in sorted(root.glob("*.py")):
            problems.extend(_module_tool_import_problems(ast.parse(path.read_text(encoding="utf-8")), path.name))
    assert problems == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("import httpx", ["ping.py must not import httpx"], id="httpx"),
        pytest.param("from httpx import AsyncClient", ["ping.py must not import httpx"], id="httpx-from"),
        pytest.param(
            "from law_scrapper_mcp.client.sejm_client import SejmApiClient",
            ["ping.py must not import client"],
            id="core-client-layer",
        ),
        pytest.param("from law_scrapper_mcp import client", ["ping.py must not import client"], id="client-package"),
    ],
)
def test_the_module_import_guard_can_actually_fail(source: str, expected: list[str]) -> None:
    assert _module_tool_import_problems(ast.parse(source), "ping.py") == expected


@pytest.mark.parametrize(
    "source",
    [
        "from law_scrapper_mcp.services.content_processor import ContentProcessor",
        "from law_scrapper_mcp.models.tool_outputs import EnrichedResponse",
        "from law_scrapper_mcp.tools.error_handling import handle_tool_errors",
        "from law_scrapper_mcp.context import AppContext, get_app_context",
        "from law_scrapper_mcp_reference.state import reference_state",
        "from .helpers import something",
    ],
)
def test_the_module_import_guard_allows_what_a_module_tool_may_use(source: str) -> None:
    assert _module_tool_import_problems(ast.parse(source), "ping.py") == []


def test_the_core_never_imports_a_source_module() -> None:
    """Modules are found by entry point. A hard import would make a disabled module part of the core."""
    checked = 0
    problems: dict[str, list[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        checked += 1
        if found := _hard_wired_module_imports(ast.parse(path.read_text(encoding="utf-8"))):
            problems[path.relative_to(SRC_ROOT).as_posix()] = found
    assert checked > EXPECTED_TOOL_COUNT, "the scan found too few files to have covered the package"
    assert problems == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("import law_scrapper_mcp_reference", ["law_scrapper_mcp_reference"], id="import"),
        pytest.param(
            "from law_scrapper_mcp_reference.state import reference_state",
            ["law_scrapper_mcp_reference.state", "law_scrapper_mcp_reference.state.reference_state"],
            id="from-submodule",
        ),
        pytest.param(
            "from law_scrapper_mcp_reference import module",
            ["law_scrapper_mcp_reference", "law_scrapper_mcp_reference.module"],
            id="from-package",
        ),
        pytest.param(
            "import importlib\nimportlib.import_module('law_scrapper_mcp_reference')",
            ["law_scrapper_mcp_reference"],
            id="import_module",
        ),
        pytest.param("__import__('law_scrapper_mcp_saos')", ["law_scrapper_mcp_saos"], id="dunder-import"),
        pytest.param("from law_scrapper_mcp import server", [], id="core-import"),
        pytest.param("import law_scrapper_mcp.source_modules.discovery", [], id="core-submodule"),
        pytest.param("import importlib\nimportlib.import_module('law_scrapper_mcp.server')", [], id="core-dynamic"),
    ],
)
def test_the_core_import_guard_can_actually_fail(source: str, expected: list[str]) -> None:
    assert _hard_wired_module_imports(ast.parse(source)) == expected
