# Source modules

Conventions for adding, activating and testing a source module. Written for contributors and for
agents that create modules; every rule below is enforced by a test or a CI gate unless it says
otherwise. The working example is `modules/reference/`: copy it to start a module.

## 1. What a module is, and what it is not

A **source module** adds tools for one legal-information source (a court-ruling database, a regional
gazette) to the server without the core knowing it exists. The core finds installed modules through a
Python entry point group, switches them on or off from one environment variable, counts their tools in
`tools/list` and in the server instructions, and gives each a place for runtime state.

A module is **not**:

- a plugin API for agents: there is no runtime enable or disable tool, only the environment variable
  read at startup;
- a way to change how the 13 core tools behave. A `source` parameter and generic document identifiers
  are out of the mechanism on purpose. The one thing a module may do to the core tools is serve further
  publisher codes through them (section 5, "Act sources"); it cannot change their names, parameters,
  defaults or output schemas;
- released with the core: the reference module is a demonstration that is never published and never in
  the container image (section 9).

## 2. Layout and naming

```
modules/<source>/
    pyproject.toml                       distribution "law-scrapper-mcp-<source>"
    README.md
    src/law_scrapper_mcp_<source>/
        __init__.py                      the descriptor: `module = SourceModule(...)`
        state.py                         typed runtime state and its accessor
        tools/__init__.py                register_all_tools(mcp)
        tools/<tool>.py                  one file per tool, same shape as the core's
    tests/                               run against the really installed distribution
```

| Thing | Rule |
|-------|------|
| Module name | `[a-z][a-z0-9-]*`; equal to the entry point name and to `SourceModule.name` |
| Distribution | `law-scrapper-mcp-<source>` (an RFC 9110 token, it becomes part of the `User-Agent`) |
| Import package | `law_scrapper_mcp_<source>`; the prefix is what ruff, mypy and the CI globs match |
| Entry point | group `law_scrapper_mcp.source_modules`, name `<source>`, value `law_scrapper_mcp_<source>:module` |
| Workspace | `modules/*` are uv workspace members (`[tool.uv.workspace]` in the root `pyproject.toml`); the module depends on `law-scrapper-mcp` with `[tool.uv.sources] law-scrapper-mcp = { workspace = true }` |

Directory name, distribution name and import package are three spellings of one name; the CI globs
(`modules/*/src/law_scrapper_mcp_*/`, `modules/*/tests`) rely on it.

## 3. The `SourceModule` descriptor

`law_scrapper_mcp.source_modules.contract.SourceModule` is a frozen dataclass. Static data lives on it
because the server instructions are composed before the server exists.

| Field | Purpose |
|-------|---------|
| `name` | Must equal the entry point name; otherwise startup fails and names the module |
| `tool_names` | Every tool `register_tools` adds. The core checks that none existed before (the SDK would silently ignore a duplicate) and that the registered set equals this tuple |
| `instructions` | Polish block appended to the server instructions when the module is active, under a header with the module name and tool count |
| `register_tools` | `Callable[[MCPServer[AppContext]], None]`, called once while the server is built |
| `start` | Optional. A factory of an async context manager, entered inside the server lifespan after the core resources exist and closed before they are. What it yields is stored in `AppContext.module_state[name]` |

The loaded object must be a `SourceModule`. A module that fails to load, a tool name that clashes with
a core tool or another module, or declared names that differ from registered ones stop startup with a
Polish message that names the module and the token that switches it off.

## 4. Tool conventions inherited from the core

A module tool is written exactly like a core tool. `tests/unit/test_tool_architecture.py` now runs its
rules over every `modules/*/src/law_scrapper_mcp_*/tools/` package:

- the decorator passes a Polish `title=` and one annotation preset from `law_scrapper_mcp.tools.annotations`
  (`READ_ONLY_REMOTE` when it calls an upstream legal-source host, `READ_ONLY_LOCAL` otherwise; a tool that
  writes needs its own named preset there);
- the handler is wrapped in `@handle_tool_errors`, reads the context only through `get_app_context`, awaits
  at most once and builds no f-string outside a hint;
- parameters use `typing.Annotated` with Polish descriptions; the docstring carries a "Kiedy użyć" /
  "Kiedy NIE używać" tree and at least five examples;
- the tool returns `EnrichedResponse[...]` with hints, so it has a native `outputSchema`.

Orchestration and response text belong in the module's own service code, not in the adapter.

## 5. Runtime state and the start hook

The start hook receives one `ModuleHost`:

| Attribute | What it is |
|-----------|-----------|
| `egress` | The `EgressHub`, the same object as `AppContext.egress` |
| `caller` | A `CallerIdentity(module, version)` the core derives from the module's distribution; a module cannot forget or spoof it |
| `settings` | The process settings |
| `act_sources` | The `ActSourceRegistry` the core services consult by publisher code (see "Act sources" below) |

A module that talks to an upstream host calls `host.egress.client_for(EgressHost(...), host.caller)`
**inside its start hook**. That call registers the host with the hub: the host appears on `/health`
under `sources`, its origin is reserved (a second claimant of the same origin fails startup), and
traffic carries the module's identity in the `User-Agent`. The hub closes the clients it created. A
module that never calls `client_for` has no host and is invisible on `/health`; the reference module
does exactly that. Limits the hub enforces on a module host: the origin pin stays on, at most three
redirects, and a rate or burst not above the defaults. Per-host TLS is set through `EgressHost.verify`.

What the start hook yields goes to `AppContext.module_state[name]`. Give the module a typed accessor
(`reference_state()` in `state.py`) so tools never index the mapping themselves.

### Act sources: serving a publisher code through the core tools

A module for a source whose acts fit the core tools (an official journal with an ELI identifier,
dates and a PDF) does not need tools of its own. It registers an `ActSource`
(`law_scrapper_mcp.services.act_sources`) in its start hook, and the core services (`search_legal_acts`,
`browse_acts`, `track_legal_changes`, `get_act_details` with its content tools, `analyze_act_relationships`,
`compare_acts`) hand a request for that publisher code to the source instead of the Sejm client.
`modules/voivodeship-journals/` is the working example, and it declares no tool (`tool_names=()`).

```python
@asynccontextmanager
async def start(host: ModuleHost) -> AsyncIterator[State]:
    client = host.egress.client_for(EgressHost(name="POL_X_AB", base_url="https://..."), host.caller)
    source = MySource({"POL_X_AB": client}, host.settings)      # implements ActSource
    host.act_sources.register(
        source,
        publishers=["POL_X_AB"],
        prefixes=("POL_X_",),
        info=[PublisherInfo(code="POL_X_AB", label="ab", available=True, item={"code": "POL_X_AB", "label": "ab"})],
    )
    try:
        yield State(...)
    finally:
        host.act_sources.unregister(source)                      # before the core closes its resources
```

What the core guarantees:

- **Empty registry, same behaviour.** The services ask the registry once per call. With nothing
  registered, or for `DU` and `MP`, they issue exactly the upstream calls they always did;
  `test_empty_registry_issues_the_same_upstream_calls` pins that. `DU` and `MP`, and any prefix that
  would cover them, cannot be claimed; a code or prefix claimed twice stops startup.
- **Exact code first, then a name, then the longest prefix.** A source that claims a prefix receives every
  code under it, including ones it does not serve, and must refuse those loudly rather than let the
  request fall through: raise `UnknownPublisherError(unknown_publisher_message(value, infos))`, the same
  text the registry itself uses (see "An unknown publisher is refused" below).
- **Descriptions and names.** `register(..., info=[PublisherInfo(...)])` lets a source describe the codes
  it registers. `label` is a name the `publisher` parameter accepts in place of the code; names are
  compared case-folded, without Polish diacritics and with space, underscore and hyphen alike, and an
  exact match only (`pomorskie` is never a match for `kujawsko-pomorskie`). `available=False` lists a code
  without offering it as valid, and `item` is the entry `get_system_metadata(category="publishers")` shows
  after DU and MP; the core does not read its keys. A label must be unique, non-empty and different from
  every code, or startup stops. `registry.describe()` returns the descriptions sorted by code.
- **An unknown publisher is refused, but only while a source is registered.** `for_publisher` raises
  `UnknownPublisherError` (a `ValueError`, so a validation error for the tool layer) for a value that is
  neither `DU`/`MP` (in any letter case) nor a code, name or prefix some source claims. The text is built
  by `unknown_publisher_message`: the echoed value is capped at 40 characters, the hint comes before the
  list, and only available publishers are listed, so the whole message fits the tool error budget
  (`error_message_max_chars`, 500) for twelve entries. With nothing registered the value goes to the Sejm as
  before.
- **A name is accepted in the `publisher` parameter only, never inside an ELI.** The core stores a loaded
  document under the ELI as the caller typed it, while every answer carries the canonical ELI, so an ELI
  with a name would load a document the next call cannot find. A source that accepts names must refuse a
  name in `get_details`, `get_relationships`, `get_pdf` and `pdf_url` and say which code to use (see
  `_resolve` in `modules/voivodeship-journals`).
- **The source returns the core output models** (`ActSummaryOutput`, `ActDetailOutput`), so paging,
  result sets, `filter_results`, size gates, section indexing and the negative cache for unavailable
  content are the core's, not the module's.
- **Content is read from the PDF.** The core downloads through `source.get_pdf(..., max_bytes=...)` under
  the usual size budget. A scan or an unreadable file is `content_status: unavailable` with a link from
  `source.pdf_url(...)`; a text layer dominated by `(cid:N)` markers loads with a WARNING in the log.
- **Errors need no mapping.** Raise a Polish `ValueError` for an input the source cannot honour,
  `ActNotFoundError` for a missing act, and the egress client's own exceptions (they carry the host
  label) for a failing host; the tool layer turns them into the usual categories.
- **`search_legal_acts` rejects a `limit` above 100 for a source** (the Sejm path keeps its documented
  lack of a ceiling). `browse_acts` and `track_legal_changes` keep their documented clamp.
- **One egress client per upstream host**, created in the start hook with `client_for`, so a failing host
  opens only its own breaker and spends only its own pacing budget (`/health` lists each one).
  `EgressClient.get_json(..., none_on_no_content=True)` returns `None` for HTTP 204, for hosts that
  answer that way for a missing resource.

A module must never send `publisher` codes it does not serve to its host, and must build the canonical
ELI (`{publisher}/{year}/{pos}`) from the request, not from fields the host returns.

## 6. Activation: `LAW_MCP_SOURCE_MODULES`

One variable. Every installed module starts **active**; the tokens are applied from left to right and
the last token that concerns a module decides:

| Token | Effect |
|-------|--------|
| `name` | switches that module on |
| `-name` | switches that module off |
| `*` | switches every installed module on |
| `-*` | switches every module off (installed or not) |

Syntax: comma-separated tokens (a JSON array is accepted like the other list settings), ASCII
whitespace around a token is trimmed, names are `[a-z][a-z0-9-]*`, `*` and `-*` only as whole tokens.
Unset, empty, whitespace-only and `[]` mean no tokens, so every installed module stays active. A module
that is switched off is **never imported**, so a broken module is disabled by naming it with a minus.

**To run only `woj`, write `-*,woj`. A bare `woj` leaves the other installed modules on.** The
container image will bake `-*` (every module installed, all off); an operator who overrides it with a
bare name replaces that value and gets every installed module. This risk is accepted knowingly; a test
named `test_bare_name_leaves_other_installed_modules_active` pins it.

Worked examples (installed: `reference`, `saos`, `woj`):

| Variable | Active modules |
|----------|----------------|
| unset, empty, whitespace or `[]` | `reference`, `saos`, `woj` |
| `-saos` | `reference`, `woj` |
| `-reference, -saos` | `woj` |
| `-*` (image default) | none (core only) |
| `-*,woj` | `woj` |
| `-*,woj,saos` | `saos`, `woj` |
| `reference,-woj` | `reference`, `saos` |
| `woj` (the accepted risk) | `reference`, `saos`, `woj` |
| `-woj,woj` | all three (the later `woj` wins) |
| `woj,-woj` | `reference`, `saos` (the later `-woj` wins) |
| `woj,-*` | none |
| `-*,woj,-woj` | none |
| `-*,-*,woj,woj` | `woj` (repetition is legal) |
| ` -* , woj ` | `woj` (padding is trimmed) |
| `*` | all three |
| `-*,*` | all three |
| `-*,*,-saos` | `reference`, `woj` |
| `woj,*` | all three |
| `*,-woj` | `reference`, `saos` |
| `-woj,*` | all three (`*` re-enables `woj`) |

**Unknown names.** A name that no installed distribution provides is a startup error only if, after
the left-to-right pass, it is still enabled (its last token is an enable): `foo`, `-*,wojj`,
`-*,woj,foo`, `foo,*` and `-foo,foo` are refused with a Polish message listing the installed names. An
unknown name that ends up disabled is accepted and reported once in a WARNING: `-foo`, `foo,-foo`,
`foo,-*`, `-*,woj,foo,-foo`. The cost, accepted deliberately: a typo in a disable token (`-saoss`) is
not caught, the module stays on and only the WARNING says so.

**Syntax errors** stop startup: an empty token inside a non-empty value (`woj,`, `,woj`, `woj,,saos`),
uppercase, `_`, an inner space, `--woj`, a lone `-`, `a*`.

At startup, when at least one module is installed, one INFO line lists the installed, active and
disabled modules and the variable as supplied. If the server does not serve the tool set you expected,
read that line first.

## 7. Gates and how to run them

Module code is linted, type-checked and tested like core code, in CI (the `lint` and `modules` jobs), in
the local mirror and by the edit hook.

```bash
# Modules are workspace members: plain `uv sync --extra dev` does NOT install them
uv sync --extra dev --all-packages

uv run ruff check src tests modules && uv run ruff format --check src tests modules
uv run mypy src/law_scrapper_mcp/ modules/*/src/law_scrapper_mcp_*/
uv run pytest modules/*/tests -v

# The root suite stays core-only whatever is installed
uv run pytest tests/unit/ && uv run pytest tests/integration/ -m integration

# Local mirror of the CI lint + test + modules jobs
uv run python scripts/check_release.py --version X.Y.Z
```

- The root suite sets `LAW_MCP_SOURCE_MODULES=-*` in `tests/conftest.py` above the first
  `law_scrapper_mcp` import and in both server subprocess environments, so an installed module never
  reaches it. Tests that need a module run from `modules/<source>/tests` against the installed
  distribution.
- Several members can be installed at once and CI collects every `modules/*/tests` in one run, so a
  module's test files carry a name no other module uses (`test_<short-name>_*.py`; two files called
  `test_install_and_disable.py` fail the collection), and shared test helpers live in an
  identically unique module (not imported from `conftest`). A test must not assume it is the only
  module installed: derive tool counts from the same configuration with your own module switched off
  (see the tests in `modules/reference/tests`) instead of hard-coding 13 or 14.
- A module's tests never reach the network. Mock the upstream host with `respx` and add an autouse
  guard that makes a name lookup for a non-local host fail (see `no_real_network` in
  `modules/voivodeship-journals/tests/conftest.py`): a test that forgets its mock then fails instead of
  sending traffic to a public institution.
- The CI `modules` job also runs the MCP conformance suite with a module active
  (`LAW_MCP_SOURCE_MODULES=-*,voivodeship-journals`), next to the core-only run in the `test` job.
- `mypy` runs with `disallow_untyped_defs`: every function in a module, tests included, is annotated.
- Import the server inside module tests (`from law_scrapper_mcp.server import build_app`), not at the top
  of the file: importing it runs discovery, so a broken descriptor should fail a named test, not the
  collection of the whole file.
- Every new test file is listed in `tests/TEST_SUITE_SUMMARY.md`.

## 8. What a module must not do

- **Shadow a core tool** or another module's tool: the core refuses it at startup.
- **Import `httpx` or `law_scrapper_mcp.client` from its `tools/` package**, or build an
  `httpx.AsyncClient` anywhere: egress goes through the host object (section 5). The construction guard
  scans `modules/` as well as `src/`.
- **Be imported by the core.** The core finds modules by entry point only; a test fails if anything under
  `src/law_scrapper_mcp/` imports a `law_scrapper_mcp_*` package.
- **Put its tools in the README tool table.** `test_readme_tool_table.py` compares that table with the
  core-only `tools/list`; module tools are documented in the module's own README.
- **Touch a core file to register itself.** A new module needs no edit in `src/`, `ci.yml` or
  `check_release.py`; if it seems to, the mechanism has a gap to fix first.
- **Ship the reference module.** See below.

## 9. Release status

`modules/reference/` (distribution `law-scrapper-mcp-reference`, version `0.0.0`) is a demonstration. It
declares an unpinned dependency on the core and is never built into release assets, attached to a release
or installed in the container image. Packaging real modules (versions pinned to the core, release assets,
image contents, `--sync-version` for members) is a separate piece of work and is not part of this
mechanism.
