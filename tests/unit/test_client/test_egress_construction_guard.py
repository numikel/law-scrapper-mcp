"""`httpx.AsyncClient` is built in exactly one place.

Every control that makes egress polite (pacing, breaker, origin pin, identity) is attached to the
client that `EgressClient.start` builds. A second construction site anywhere in `src/` would be a
channel that bypasses all of them, so the rule is enforced by reading the source, not by review.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src" / "law_scrapper_mcp"
MODULES = REPO / "modules"
# The scan roots, written out. A source module is code too: if it could build its own client it
# would bypass pacing, breaker, origin pin and identity just like a second site in `src/`.
ROOTS = (SRC, MODULES)
ALLOWED = SRC / "client" / "egress.py"
# Traffic to the operator's own identity provider (OIDC discovery), not to an upstream data
# host: it is configured by the operator, carries no user query and has nothing to pace per
# source. Listed by file and exact count, so a second client there would fail this test too.
OTHER_SITES = {Path("src") / "law_scrapper_mcp" / "auth" / "jwt_verifier.py": 1}


def roots_without_python_files(roots: tuple[Path, ...]) -> list[Path]:
    """Roots that hold no `.py` file: a missing or renamed root would make the scan pass vacuously."""
    return [root for root in roots if not any(root.rglob("*.py"))]


def construction_sites(source: str) -> list[int]:
    """Line numbers where the source calls `AsyncClient(...)`, whether as `httpx.AsyncClient` or bare."""
    sites = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "AsyncClient") or (
            isinstance(func, ast.Name) and func.id == "AsyncClient"
        ):
            sites.append(node.lineno)
    return sites


def test_the_detector_finds_both_spellings() -> None:
    assert construction_sites("import httpx\nc = httpx.AsyncClient(timeout=1)\n") == [2]
    assert construction_sites("from httpx import AsyncClient\nc = AsyncClient()\n") == [2]
    assert construction_sites("import httpx\nx = httpx.Client()\n") == []


def test_the_root_check_flags_an_empty_or_missing_root(tmp_path: Path) -> None:
    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "a.py").write_text("x = 1\n", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = tmp_path / "missing"

    assert roots_without_python_files((populated, empty, missing)) == [empty, missing]


def test_every_scan_root_holds_python_files() -> None:
    assert roots_without_python_files(ROOTS) == []


def test_only_the_egress_module_constructs_an_httpx_async_client() -> None:
    found = {
        path.relative_to(REPO): len(sites)
        for root in ROOTS
        for path in sorted(root.rglob("*.py"))
        if path != ALLOWED and (sites := construction_sites(path.read_text(encoding="utf-8")))
    }

    assert found == OTHER_SITES


def test_the_egress_module_does_construct_one() -> None:
    assert len(construction_sites(ALLOWED.read_text(encoding="utf-8"))) == 1
