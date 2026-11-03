# law-scrapper-mcp-voivodeship-journals

A source module for Law Scrapper MCP that serves acts of the voivodeship official journals
(e-Dziennik ELI) through the tools the server already has. It adds no tool: `search_legal_acts`,
`browse_acts`, `track_legal_changes`, `get_act_details`, `read_act_content`, `search_in_act`,
`analyze_act_relationships` and `compare_acts` accept the publisher codes `POL_WOJ_*`, for example
`search_legal_acts(publisher="POL_WOJ_PM", year=2025, title="budżet")` or
`get_act_details(eli="POL_WOJ_PM/2025/5051", load_content=True)`.

The journals are separate sites on one engine, 16 in all. Each answers only for its own acts, so the
module keeps a table from publisher code to host (`catalog.py`) and talks to exactly one host per
request.

## Install and enable

```bash
# The module is a uv workspace member: plain `uv sync --extra dev` does NOT install it
uv sync --extra dev --all-packages

# Module tests (against the installed distribution; they never reach the network)
.venv/Scripts/python.exe -m pytest modules/voivodeship-journals/tests -v
```

Every installed module is active unless switched off by `LAW_MCP_SOURCE_MODULES`.
`-voivodeship-journals` switches this one off (the package is then never imported), and
`-*,voivodeship-journals` runs only this module. A bare `voivodeship-journals` leaves every other
installed module on.

## Served and withheld journals

Twelve journals are served. Four are listed but withheld: a request for their code fails with the
reason below and no request is sent to their hosts.

| Code | Voivodeship | Host | State |
|------|-------------|------|-------|
| `POL_WOJ_DS` | dolnośląskie | edzienniki.duw.pl | served |
| `POL_WOJ_KP` | kujawsko-pomorskie | edzienniki.bydgoszcz.uw.gov.pl | served |
| `POL_WOJ_LB` | lubelskie | edziennik.lublin.uw.gov.pl | served |
| `POL_WOJ_LS` | lubuskie | dzienniki.luw.pl | withheld: incomplete TLS certificate chain |
| `POL_WOJ_LD` | łódzkie | dziennik.lodzkie.eu | withheld: incomplete TLS certificate chain |
| `POL_WOJ_MP` | małopolskie | edziennik.malopolska.uw.gov.pl | withheld: incomplete TLS certificate chain |
| `POL_WOJ_MZ` | mazowieckie | edziennik.mazowieckie.pl | withheld: the server did not answer 8 of 8 attempts on 2026-11-01 and 2026-11-02 |
| `POL_WOJ_OP` | opolskie | duwo.opole.uw.gov.pl | served |
| `POL_WOJ_PK` | podkarpackie | edziennik.rzeszow.uw.gov.pl | served |
| `POL_WOJ_PL` | podlaskie | edziennik.bialystok.uw.gov.pl | served |
| `POL_WOJ_PM` | pomorskie | edziennik.gdansk.uw.gov.pl | served |
| `POL_WOJ_SL` | śląskie | dzienniki.slask.eu | served |
| `POL_WOJ_SK` | świętokrzyskie | edziennik.kielce.uw.gov.pl | served |
| `POL_WOJ_WM` | warmińsko-mazurskie | edzienniki.olsztyn.uw.gov.pl | served |
| `POL_WOJ_WP` | wielkopolskie | edziennik.poznan.uw.gov.pl | served |
| `POL_WOJ_ZP` | zachodniopomorskie | e-dziennik.szczecin.uw.gov.pl | served |

The three TLS cases would need a bundled certificate decision; flipping `withheld` in `catalog.py`
is the whole change on the catalogue side once that is decided. A code under the prefix that is in
no row (`POL_WOJ_XX`) is refused with the list of served codes.

## What a journal does not support

These requests fail with a Polish message that names the problem, before any request is sent:

- `in_force`: the journals keep no legal-status data. Results carry no effective date either.
- `date_from` and `date_to` (entry-into-force dates). Use `pub_date_from` and `pub_date_to`, the
  announcement dates, which the journals filter.
- More than one keyword. A recorded probe (three requests to one journal, see
  `tests/fixtures/edziennik/PROVENANCE.md`) showed that a comma-joined list does not narrow the
  result the way the Sejm's AND does, so the module does not pass one on. Search each keyword
  separately.
- `limit` above 100 in `search_legal_acts`. The journals have no page ceiling of their own: one
  host answered a request for a whole year with 5 396 records in 3 MB. Page with `offset`.

Searching without `year` is slow on these hosts (5 to 10 seconds measured), so pass it.
`track_legal_changes` works: it asks for the acts announced in the window.

## Content

Text is read from the PDF, never from `text.html`, which is only the first page of the PDF. A scan
without a text layer gives `content_status: unavailable` and a link to the journal's PDF. Relations
come back under the journal's own relation descriptions, not under the keys DU and MP use.

Some older PDFs have a text layer whose glyphs have no Unicode mapping, which extracts as
`(cid:N)` markers. Such an act is loaded anyway and a WARNING naming it goes to the server log; the
response does not flag it, and the module instructions tell the agent to compare old acts with the
PDF. The detection (at least 10 markers and at least 0.5% of the characters) is an assumption
checked only against a stand-in PDF reader, because no real journal PDF with this defect was found
among the recordings. Treat both thresholds as unverified.

## Which hosts see your queries

A request goes only to the journal of the voivodeship you chose, and carries what you searched for:
the year, the title fragment, the type, a keyword, the announcement dates, or the act's number. No
request goes to the Sejm for a `POL_WOJ_*` code.

## Terms of use

No terms of use for the journals' APIs were found (Swagger `termsOfService` empty in all 30 recorded
documents; the pages of the register linked none; checked 2026-11-01 and 2026-11-02). No permission
was requested or obtained. The module identifies itself honestly (name, version, contact) and sends
at most 5 requests per second to one host.

If you intend commercial and/or mass use, verify again that using these APIs is permitted. This does
not concern private use or use by a sole proprietorship (JDG).
