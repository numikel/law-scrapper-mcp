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
"""

import ast
from pathlib import Path

import pytest

from mcp_helpers import EXPECTED_TOOLS

TOOLS_ROOT = Path(__file__).parents[2] / "src" / "law_scrapper_mcp" / "tools"
SKIPPED_MODULES = {"__init__.py", "error_handling.py"}
MAX_AWAITS_PER_HANDLER = 1
HINT_FACTORIES = {"Hint"}
PRESET_MODULE = "law_scrapper_mcp.tools.annotations"
PRESETS = {"READ_ONLY_REMOTE", "READ_ONLY_LOCAL"}
EXPECTED_TOOL_COUNT = len(EXPECTED_TOOLS)

# Messages and import line used by the guard-the-guard cases below.
ANNOTATIONS_PROBLEM = f"tool: annotations= must name a preset imported from {PRESET_MODULE}"
TITLE_PROBLEM = "tool: title= must be a non-empty string literal"
IMPORT_LOCAL = f"from {PRESET_MODULE} import READ_ONLY_LOCAL"


def _tool_modules() -> list[Path]:
    return sorted(path for path in TOOLS_ROOT.glob("*.py") if path.name not in SKIPPED_MODULES)


def _handlers(tree: ast.Module) -> list[ast.AsyncFunctionDef]:
    """Return every `@mcp.tool(...)`-decorated coroutine in the module."""
    found: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                found.append(node)
                break
    return found


def _is_hint_factory(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name in HINT_FACTORIES or name.endswith("_hints")


def _body_nodes(handler: ast.AsyncFunctionDef) -> list[ast.AST]:
    """Walk the handler body only.

    The signature is excluded on purpose: `Field(description=f"...")` on a tool
    parameter is protocol documentation, not response text.
    """
    nodes: list[ast.AST] = []
    for statement in handler.body:
        nodes.extend(ast.walk(statement))
    return nodes


def _formatted_strings_outside_hints(handler: ast.AsyncFunctionDef) -> list[int]:
    """Line numbers of f-strings that are not part of building a hint."""
    body = _body_nodes(handler)
    allowed: set[int] = set()
    for node in body:
        if _is_hint_factory(node):
            allowed.update(id(inner) for inner in ast.walk(node) if isinstance(inner, ast.JoinedStr))
    return [node.lineno for node in body if isinstance(node, ast.JoinedStr) and id(node) not in allowed]


def _tool_decorators(tree: ast.Module) -> list[tuple[str, ast.Call]]:
    """Return (handler name, `@mcp.tool(...)` call) for every tool in the module."""
    found: list[tuple[str, ast.Call]] = []
    for handler in _handlers(tree):
        for decorator in handler.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                found.append((handler.name, decorator))
    return found


def _imported_presets(tree: ast.Module) -> set[str]:
    """Every expression that names a shared preset in this module, in `ast.unparse` spelling.

    `from <PRESET_MODULE> import READ_ONLY_LOCAL as RO` yields the local name `RO`.
    Importing the module itself (`import <PRESET_MODULE> [as ann]` or
    `from law_scrapper_mcp.tools import annotations [as ann]`) yields
    `<prefix>.READ_ONLY_LOCAL` and `<prefix>.READ_ONLY_REMOTE`. Relative imports are
    not recognised; the tool modules import absolutely.
    """
    package, _, module = PRESET_MODULE.rpartition(".")
    references: set[str] = set()
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == PRESET_MODULE and alias.name in PRESETS:
                    references.add(alias.asname or alias.name)
                elif node.module == package and alias.name == module:
                    prefixes.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            prefixes.update(alias.asname or alias.name for alias in node.names if alias.name == PRESET_MODULE)
    return references | {f"{prefix}.{preset}" for prefix in prefixes for preset in PRESETS}


def _decorator_problems(tree: ast.Module) -> list[str]:
    """A tool must pick a shared preset on purpose and carry a human title."""
    presets = _imported_presets(tree)
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


def _tool_module(imports: str, arguments: str) -> ast.Module:
    """Parse a module with the given import lines and one `@mcp.tool(<arguments>)` handler named `tool`."""
    return ast.parse(
        "\n".join(
            [
                imports,
                "def register(mcp):",
                f"    @mcp.tool({arguments})",
                "    async def tool(ctx):",
                "        return None",
            ]
        )
    )


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


def test_every_tool_declares_an_annotation_preset_and_a_title() -> None:
    """A future write tool cannot inherit read-only hints without a deliberate choice."""
    total = 0
    for path in _tool_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += len(_tool_decorators(tree))
        problems = _decorator_problems(tree)
        assert problems == [], f"{path.name}: {problems}"
    assert total == EXPECTED_TOOL_COUNT


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
