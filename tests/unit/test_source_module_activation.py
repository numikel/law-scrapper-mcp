"""LAW_MCP_SOURCE_MODULES: syntax, left-to-right evaluation and unknown names.

The case table mirrors the worked examples in docs/source-modules.md, section "Activation".
Installed modules throughout: `reference`, `saos`, `woj`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fake_source_modules import spy_entry_points
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.source_modules.contract import SourceModuleError
from law_scrapper_mcp.source_modules.discovery import discover, resolve_activation

INSTALLED = ("reference", "saos", "woj")
ALL = INSTALLED
NO_MODULES: tuple[str, ...] = ()


def settings_for(value: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("LAW_MCP_SOURCE_MODULES", raising=False)
    return Settings(source_modules=value)  # type: ignore[arg-type]


# (variable, active modules) - rows 1-20 of the grammar table.
KNOWN_NAME_CASES: list[tuple[str, tuple[str, ...]]] = [
    ("", ALL),
    ("   ", ALL),
    ("[]", ALL),
    ("-saos", ("reference", "woj")),
    ("-reference, -saos", ("woj",)),
    ("-*", NO_MODULES),
    ("-*,woj", ("woj",)),
    ("-*,woj,saos", ("saos", "woj")),
    ("reference,-woj", ("reference", "saos")),
    ("woj", ALL),
    ("-woj,woj", ALL),
    ("woj,-woj", ("reference", "saos")),
    ("woj,-*", NO_MODULES),
    ("-*,woj,-woj", NO_MODULES),
    ("-*,-*,woj,woj", ("woj",)),
    (" -* , woj ", ("woj",)),
    ("*", ALL),
    ("-*,*", ALL),
    ("-*,*,-saos", ("reference", "woj")),
    ("woj,*", ALL),
    ("*,-woj", ("reference", "saos")),
    ("-woj,*", ALL),
    ('["-*", "woj"]', ("woj",)),
]


@pytest.mark.parametrize(("value", "expected"), KNOWN_NAME_CASES)
def test_resolve_activation_follows_the_grammar_table(
    value: str, expected: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = settings_for(value, monkeypatch).source_modules

    result = resolve_activation(tokens, INSTALLED)

    assert result.active == expected
    assert result.inactive == tuple(name for name in INSTALLED if name not in expected)
    assert result.unknown_enabled == ()
    assert result.unknown_ignored == ()


@pytest.mark.parametrize(("value", "expected"), KNOWN_NAME_CASES)
def test_discover_loads_exactly_the_active_modules(
    value: str, expected: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validator and the evaluation agree, and a switched-off module is never imported."""
    spies = spy_entry_points(*INSTALLED)

    discovery = discover(settings_for(value, monkeypatch), [spy.entry_point for spy in spies.values()])

    assert tuple(item.module.name for item in discovery.active) == expected
    assert discovery.inactive == tuple(name for name in INSTALLED if name not in expected)
    assert {name for name, spy in spies.items() if spy.loads} == set(expected)


def test_unset_variable_activates_every_installed_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAW_MCP_SOURCE_MODULES", raising=False)

    assert resolve_activation(Settings().source_modules, INSTALLED).active == ALL


def test_bare_name_leaves_other_installed_modules_active() -> None:
    """The accepted risk: a bare name is NOT an allowlist.

    The container image is meant to bake `-*`; an operator who overrides it with `woj` replaces that
    value and gets every installed module. To run only `woj` the value must be `-*,woj`. This test
    exists so the behaviour cannot change unnoticed in either direction (docs/source-modules.md,
    section "Activation").
    """
    assert resolve_activation(["woj"], INSTALLED).active == ALL
    assert resolve_activation(["-*", "woj"], INSTALLED).active == ("woj",)


def test_image_default_runs_no_module_whatever_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The value the container image is meant to bake must mean "core only" for any installed set."""
    monkeypatch.setenv("LAW_MCP_SOURCE_MODULES", "-*")

    tokens = Settings().source_modules

    assert tokens == ["-*"]
    for installed in (INSTALLED, ("only-one",), ("a", "b", "c", "d")):
        assert resolve_activation(tokens, installed).active == ()
        assert resolve_activation(tokens, installed).inactive == tuple(sorted(installed))


def test_token_order_decides() -> None:
    assert resolve_activation(["-woj", "woj"], INSTALLED).active == ALL
    assert resolve_activation(["woj", "-woj"], INSTALLED).active == ("reference", "saos")
    assert resolve_activation(["-*", "woj"], INSTALLED).active == ("woj",)
    assert resolve_activation(["woj", "-*"], INSTALLED).active == ()
    assert resolve_activation(["*", "-woj"], INSTALLED).active == ("reference", "saos")
    assert resolve_activation(["-woj", "*"], INSTALLED).active == ALL


def test_active_set_is_sorted_whatever_the_token_order() -> None:
    assert resolve_activation(["-*", "woj", "reference", "saos"], INSTALLED).active == INSTALLED
    assert resolve_activation(["-*", "saos", "reference"], INSTALLED).active == ("reference", "saos")


@pytest.mark.parametrize(
    "value",
    ["woj,woj", "-*,-*", "woj,-woj", "-woj,-woj", "*,*", "*"],
)
def test_repetition_and_overriding_are_not_errors(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result = resolve_activation(settings_for(value, monkeypatch).source_modules, INSTALLED)

    assert result.unknown_enabled == ()


# --- syntax errors: refused when Settings is built ---------------------------------------------


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("Woj", "'Woj'"),
        ("woj_x", "'woj_x'"),
        ("woj saos", "'woj saos'"),
        ("--woj", "'--woj'"),
        ("-", "'-'"),
        ("a*", "'a*'"),
        ("**", "'**'"),
        ("1woj", "'1woj'"),
        ("woj,,saos", "pusty token"),
        ("woj,", "pusty token"),
        (",woj", "pusty token"),
        ('[""]', "pusty token"),
        ("[1]", "nieprawidłowy token"),
    ],
)
def test_malformed_value_is_refused_naming_the_variable(
    value: str, fragment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        settings_for(value, monkeypatch)

    message = str(excinfo.value)
    assert "LAW_MCP_SOURCE_MODULES" in message
    assert fragment in message


@pytest.mark.parametrize("value", ["*", "-*", "woj", "-woj", "a-b-c", "w1"])
def test_whole_star_tokens_and_well_formed_names_pass_the_validator(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings_for(value, monkeypatch).source_modules == [value]


def test_env_value_is_read_as_comma_separated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAW_MCP_SOURCE_MODULES", "-*, woj ,saos")

    assert Settings().source_modules == ["-*", "woj", "saos"]


def test_env_value_accepts_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAW_MCP_SOURCE_MODULES", '["-*", "woj"]')

    assert Settings().source_modules == ["-*", "woj"]


# --- unknown names: an error only when effectively unknown -------------------------------------

REFUSED = ["foo", "-*,wojj", "-*,woj,foo", "foo,*", "-foo,foo"]
ACCEPTED_WITH_WARNING: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("-foo", ALL, ("foo",)),
    ("foo,-foo", ALL, ("foo",)),
    ("foo,-*", NO_MODULES, ("foo",)),
    ("-*,woj,foo,-foo", ("woj",), ("foo",)),
    ("-saoss", ALL, ("saoss",)),
]


@pytest.mark.parametrize("value", REFUSED)
def test_unknown_name_that_stays_enabled_is_refused(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = settings_for(value, monkeypatch).source_modules

    assert resolve_activation(tokens, INSTALLED).unknown_enabled

    spies = spy_entry_points(*INSTALLED)
    with pytest.raises(SourceModuleError) as excinfo:
        discover(settings_for(value, monkeypatch), [spy.entry_point for spy in spies.values()])
    message = str(excinfo.value)
    assert "LAW_MCP_SOURCE_MODULES" in message
    assert "reference, saos, woj" in message  # the installed names, to find the typo
    assert not any(spy.loads for spy in spies.values()), "nothing is imported once the value is refused"


@pytest.mark.parametrize(("value", "expected", "ignored"), ACCEPTED_WITH_WARNING)
def test_unknown_name_that_ends_disabled_is_accepted_and_reported(
    value: str, expected: tuple[str, ...], ignored: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = settings_for(value, monkeypatch).source_modules

    result = resolve_activation(tokens, INSTALLED)

    assert result.unknown_enabled == ()
    assert result.active == expected
    assert result.unknown_ignored == ignored

    spies = spy_entry_points(*INSTALLED)
    discovery = discover(settings_for(value, monkeypatch), [spy.entry_point for spy in spies.values()])
    assert discovery.ignored_unknown == ignored
    assert tuple(item.module.name for item in discovery.active) == expected


@pytest.mark.parametrize("position", [0, 1, 2])
def test_unknown_name_is_judged_by_its_last_token_at_any_position(position: int) -> None:
    """`foo` and `-foo` in the first, a middle and the last position of `-saos,woj`."""
    base = ["-saos", "woj"]

    enabled = [*base[:position], "foo", *base[position:]]
    disabled = [*base[:position], "-foo", *base[position:]]

    assert resolve_activation(enabled, INSTALLED).unknown_enabled == ("foo",)
    accepted = resolve_activation(disabled, INSTALLED)
    assert accepted.unknown_enabled == ()
    assert accepted.unknown_ignored == ("foo",)
    assert accepted.active == ("reference", "woj")


def test_unknown_name_with_no_module_installed() -> None:
    assert resolve_activation(["foo"], ()).unknown_enabled == ("foo",)
    assert resolve_activation(["-foo"], ()).unknown_ignored == ("foo",)
    assert resolve_activation(["-*"], ()).active == ()
