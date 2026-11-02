"""The root suite is core-only, whatever source modules the environment has installed.

`tests/conftest.py` sets `LAW_MCP_SOURCE_MODULES=-*` above the first `law_scrapper_mcp` import. These
tests pin that isolation itself, so deleting the `conftest.py` line fails a named test even on a
machine that installed no module (where every other test would still pass by accident).
"""

from __future__ import annotations

import os

from law_scrapper_mcp.config import settings
from law_scrapper_mcp.source_modules.discovery import installed_entry_points, resolve_activation
from mcp_helpers import CORE_ONLY_ENV


def test_the_process_environment_switches_every_module_off() -> None:
    assert os.environ["LAW_MCP_SOURCE_MODULES"] == "-*"
    assert CORE_ONLY_ENV == {"LAW_MCP_SOURCE_MODULES": "-*"}


def test_the_live_settings_were_built_with_the_core_only_value() -> None:
    """`settings` is read at import, so this fails if the variable was set too late."""
    assert settings.source_modules == ["-*"]


def test_no_installed_module_is_active_in_the_root_suite() -> None:
    """Over the entry points really installed here (none, or the reference module)."""
    installed = [entry_point.name for entry_point in installed_entry_points()]

    activation = resolve_activation(settings.source_modules, installed)

    assert activation.active == ()
    assert activation.inactive == tuple(sorted(installed))
