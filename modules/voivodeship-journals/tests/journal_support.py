"""Helpers shared by the module tests: the recorded journal payloads and the hosts they came from.

A separate module, not `conftest`, so tests can import from it by a name no other module's tests use
(several module test directories are collected in one run).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from law_scrapper_mcp.client.egress import CallerIdentity, EgressHub
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.source_modules.contract import ModuleHost

FIXTURES = Path(__file__).parents[3] / "tests" / "fixtures" / "edziennik"
CALLER = CallerIdentity(module="law-scrapper-mcp-voivodeship-journals", version="0.0.0")

PM = "https://edziennik.gdansk.uw.gov.pl/api/eli"
KP = "https://edzienniki.bydgoszcz.uw.gov.pl/api/eli"
SK = "https://edziennik.kielce.uw.gov.pl/api/eli"
MZ = "https://edziennik.mazowieckie.pl/api/eli"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@dataclass
class Started:
    """The module after its start hook ran."""

    hub: EgressHub
    registry: ActSourceRegistry
    host: ModuleHost
