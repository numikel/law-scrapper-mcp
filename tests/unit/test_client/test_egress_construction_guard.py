"""`httpx.AsyncClient` is built in exactly one place.

Every control that makes egress polite (pacing, breaker, origin pin, identity) is attached to the
client that `EgressClient.start` builds. A second construction site anywhere in `src/` would be a
channel that bypasses all of them, so the rule is enforced by reading the source, not by review.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "law_scrapper_mcp"
ALLOWED = SRC / "client" / "egress.py"
# Traffic to the operator's own identity provider (OIDC discovery), not to an upstream data
# host: it is configured by the operator, carries no user query and has nothing to pace per
# source. Listed by file and exact count, so a second client there would fail this test too.
OTHER_SITES = {Path("auth") / "jwt_verifier.py": 1}


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


def test_only_the_egress_module_constructs_an_httpx_async_client() -> None:
    found = {
        path.relative_to(SRC): len(sites)
        for path in sorted(SRC.rglob("*.py"))
        if path != ALLOWED and (sites := construction_sites(path.read_text(encoding="utf-8")))
    }

    assert found == OTHER_SITES


def test_the_egress_module_does_construct_one() -> None:
    assert len(construction_sites(ALLOWED.read_text(encoding="utf-8"))) == 1
