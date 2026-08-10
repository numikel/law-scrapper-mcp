"""Architecture guards for thin MCP adapters."""

from pathlib import Path

TOOLS_ROOT = Path(__file__).parents[2] / "src" / "law_scrapper_mcp" / "tools"


def test_tools_do_not_use_untyped_lifespan_keys_or_store_results_directly() -> None:
    for path in TOOLS_ROOT.glob("*.py"):
        if path.name in {"error_handling.py", "__init__.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert 'lifespan_context["' not in source, path
        assert "lifespan_context." not in source, path
        assert ".store(" not in source, path
        if "ctx" in source:
            assert "get_app_context" in source, path
