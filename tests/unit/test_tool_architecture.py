"""Architecture guards for thin MCP adapters.

The previous version of this guard matched substrings, so it only constrained
*how* a handler reached the lifespan context. It passed for handlers that
looped over domain data and formatted user-facing text. These checks parse the
adapters instead and constrain *what* a handler is allowed to do.

Known limit: model-mapping comprehensions (`[Model.model_validate(d) for d in
raw]`) are still allowed, because turning a store's dict into its output model
is adapter work, not domain work.
"""

import ast
from pathlib import Path

TOOLS_ROOT = Path(__file__).parents[2] / "src" / "law_scrapper_mcp" / "tools"
SKIPPED_MODULES = {"__init__.py", "error_handling.py"}
MAX_AWAITS_PER_HANDLER = 1
HINT_FACTORIES = {"Hint"}


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
