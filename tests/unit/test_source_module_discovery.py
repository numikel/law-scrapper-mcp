"""Discovery: reading entry points, validating descriptors, loading only what is active."""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_source_modules import SpyEntryPoint, fake_module, spy_entry_points
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.source_modules.contract import ENTRY_POINT_GROUP, SourceModuleError
from law_scrapper_mcp.source_modules.discovery import (
    InstalledEntryPoint,
    discover,
    installed_entry_points,
    registered_tool_names,
)


def settings_for(value: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("LAW_MCP_SOURCE_MODULES", raising=False)
    return Settings(source_modules=value)  # type: ignore[arg-type]


def entry_points_of(*spies: SpyEntryPoint) -> list[InstalledEntryPoint]:
    return [spy.entry_point for spy in spies]


def test_no_installed_module_is_an_empty_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    discovery = discover(settings_for("", monkeypatch), [])

    assert discovery.active == ()
    assert discovery.inactive == ()
    assert discovery.requested == ()


def test_discovery_keeps_the_supplied_tokens_for_the_startup_log(monkeypatch: pytest.MonkeyPatch) -> None:
    spies = spy_entry_points("alpha", "beta")

    discovery = discover(settings_for("-*, beta", monkeypatch), entry_points_of(*spies.values()))

    assert discovery.requested == ("-*", "beta")
    assert [item.module.name for item in discovery.active] == ["beta"]
    assert discovery.inactive == ("alpha",)


def test_active_modules_carry_their_distribution_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = SpyEntryPoint("alpha", fake_module("alpha"), distribution="law-scrapper-mcp-alpha", version="1.2.3+local")

    (item,) = discover(settings_for("", monkeypatch), entry_points_of(spy)).active

    assert (item.distribution, item.version) == ("law-scrapper-mcp-alpha", "1.2.3+local")


def test_switched_off_module_is_never_imported_even_when_it_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """The remedy for a broken module has to work: disabling it must skip its import entirely."""
    healthy = SpyEntryPoint("alpha", fake_module("alpha"))
    broken = SpyEntryPoint("broken", ImportError("boom"))

    discovery = discover(settings_for("-broken", monkeypatch), entry_points_of(healthy, broken))

    assert broken.loads == []
    assert healthy.loads == ["alpha"]
    assert [item.module.name for item in discovery.active] == ["alpha"]


def test_nothing_is_imported_after_minus_star_unless_reenabled(monkeypatch: pytest.MonkeyPatch) -> None:
    spies = spy_entry_points("alpha", "beta")

    discover(settings_for("-*", monkeypatch), entry_points_of(*spies.values()))
    assert [spy.loads for spy in spies.values()] == [[], []]

    discover(settings_for("-*,beta", monkeypatch), entry_points_of(*spies.values()))
    assert spies["alpha"].loads == []
    assert spies["beta"].loads == ["beta"]


def test_inactive_module_is_not_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A descriptor with the wrong name is only an error while the module is active."""
    wrong = SpyEntryPoint("alpha", fake_module("other"))

    discover(settings_for("-alpha", monkeypatch), entry_points_of(wrong))

    assert wrong.loads == []


def test_duplicate_entry_point_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    first = SpyEntryPoint("alpha", fake_module("alpha"), distribution="pkg-one")
    second = SpyEntryPoint("alpha", fake_module("alpha"), distribution="pkg-two")

    with pytest.raises(SourceModuleError, match="alpha"):
        discover(settings_for("", monkeypatch), entry_points_of(first, second))


def test_entry_point_that_is_not_a_descriptor_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SourceModuleError) as excinfo:
        discover(settings_for("", monkeypatch), entry_points_of(SpyEntryPoint("alpha", object())))

    assert "alpha" in str(excinfo.value)
    assert "SourceModule" in str(excinfo.value)
    assert "-alpha" in str(excinfo.value)


def test_descriptor_name_must_equal_the_entry_point_name(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SourceModuleError) as excinfo:
        discover(settings_for("", monkeypatch), entry_points_of(SpyEntryPoint("alpha", fake_module("beta"))))

    assert "alpha" in str(excinfo.value)
    assert "beta" in str(excinfo.value)


def test_load_failure_names_the_module_and_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SourceModuleError) as excinfo:
        discover(settings_for("", monkeypatch), entry_points_of(SpyEntryPoint("alpha", ImportError("brak zależności"))))

    message = str(excinfo.value)
    assert "alpha" in message
    assert "brak zależności" in message
    assert "LAW_MCP_SOURCE_MODULES" in message
    assert "-alpha" in message
    assert isinstance(excinfo.value.__cause__, ImportError)


INVALID_ENTRY_POINT_NAMES = ["Alpha", "al_pha", "1alpha", "al pha", "al.pha", "-alpha", "alpha*", "", "*"]


@pytest.mark.parametrize("variable", ["", "-*", "-*,beta", "-alpha"])
@pytest.mark.parametrize("name", INVALID_ENTRY_POINT_NAMES)
def test_invalid_entry_point_name_is_refused_before_any_import(
    name: str, variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name the variable cannot spell has no `-name` remedy; no module code may run on the way."""
    bad = SpyEntryPoint(name, fake_module("alpha"), distribution="law-scrapper-mcp-bad")
    healthy = SpyEntryPoint("beta", fake_module("beta"))

    with pytest.raises(SourceModuleError) as excinfo:
        discover(settings_for(variable, monkeypatch), entry_points_of(healthy, bad))

    message = str(excinfo.value)
    assert "law-scrapper-mcp-bad" in message, "names the distribution to uninstall"
    assert "odinstaluj" in message
    assert "dopisując" not in message and "Wyłącz" not in message, "no disable token is suggested"
    assert bad.loads == []
    assert healthy.loads == [], "nothing is imported once any name is invalid"


def test_tool_names_may_not_repeat_within_a_module(monkeypatch: pytest.MonkeyPatch) -> None:
    repeated = fake_module("alpha", ["alpha_ping", "alpha_ping"])

    with pytest.raises(SourceModuleError, match="alpha"):
        discover(settings_for("", monkeypatch), entry_points_of(SpyEntryPoint("alpha", repeated)))


def test_two_modules_may_not_declare_the_same_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    alpha = SpyEntryPoint("alpha", fake_module("alpha", ["shared_tool"]))
    beta = SpyEntryPoint("beta", fake_module("beta", ["shared_tool"]))

    with pytest.raises(SourceModuleError) as excinfo:
        discover(settings_for("", monkeypatch), entry_points_of(alpha, beta))

    message = str(excinfo.value)
    assert {"alpha", "beta", "shared_tool"} <= set(message.replace(",", " ").replace(".", " ").split())


def test_the_clash_disappears_when_one_of_the_modules_is_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    alpha = SpyEntryPoint("alpha", fake_module("alpha", ["shared_tool"]))
    beta = SpyEntryPoint("beta", fake_module("beta", ["shared_tool"]))

    discovery = discover(settings_for("-beta", monkeypatch), entry_points_of(alpha, beta))

    assert [item.module.name for item in discovery.active] == ["alpha"]


# --- reading the real environment --------------------------------------------------------------


def write_distribution(site: Path, *, dist_name: str, version: str, entry_name: str, target: str) -> None:
    dist_info = site / f"{dist_name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: {version}\n", encoding="utf-8"
    )
    (dist_info / "entry_points.txt").write_text(f"[{ENTRY_POINT_GROUP}]\n{entry_name} = {target}\n", encoding="utf-8")


def test_installed_entry_points_read_name_and_version_from_the_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_distribution(
        tmp_path,
        dist_name="law-scrapper-mcp-fakesrc",
        version="2.3.4",
        entry_name="fakesrc",
        target="fakesrc_pkg:module",
    )
    (tmp_path / "fakesrc_pkg.py").write_text(
        "from fake_source_modules import fake_module\nmodule = fake_module('fakesrc')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "fakesrc_pkg", raising=False)

    (found,) = [item for item in installed_entry_points() if item.name == "fakesrc"]

    assert (found.distribution, found.version) == ("law-scrapper-mcp-fakesrc", "2.3.4")
    assert "fakesrc_pkg" not in sys.modules, "reading the entry points must not import the module"

    discovery = discover(settings_for("-*,fakesrc", monkeypatch))
    assert [item.module.name for item in discovery.active] == ["fakesrc"]
    assert discovery.active[0].distribution == "law-scrapper-mcp-fakesrc"
    monkeypatch.delitem(sys.modules, "fakesrc_pkg", raising=False)


def test_entry_point_without_a_distribution_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    orphan = SimpleNamespace(name="orphan", dist=None, load=lambda: None)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_kwargs: [orphan])

    with pytest.raises(SourceModuleError, match="orphan"):
        installed_entry_points()


# --- guard the guard ---------------------------------------------------------------------------


def test_registered_tool_names_lists_the_tools_of_a_server() -> None:
    from law_scrapper_mcp.server import app

    assert {"search_legal_acts", "browse_acts"} <= registered_tool_names(app)


def test_registered_tool_names_fails_loudly_when_the_sdk_attribute_moves() -> None:
    """If an `mcp` upgrade renames the private tool manager this must not return an empty set."""
    with pytest.raises(RuntimeError, match="registered_tool_names"):
        registered_tool_names(SimpleNamespace())  # type: ignore[arg-type]
