# law-scrapper-mcp-saos

A source module for Law Scrapper MCP that searches Polish court judgments in SAOS (System Analizy
Orzeczeń Sądowych, `www.saos.org.pl`), an academic service that republishes judgments of common courts,
the Supreme Court, the Constitutional Tribunal and the National Appeal Chamber. It adds two tools,
named after the source and without a `source` parameter:

| Tool | What it does |
|------|--------------|
| `search_saos_judgments` | Searches judgments newest first, with any `limit` (default 20, at most 100) and any `offset`. Stores the page as a result set that the core `filter_results` narrows. |
| `get_saos_judgment_details` | One request about one judgment: its record, the court portal address and the division type. With `load_content=true` it also loads the judgment's text into the document store (up to 3 requests), where `read_act_content` and `search_in_act` read it as `SAOS/<source_id>`. |

The 13 core tools keep their names, parameters and output schemas. Tool descriptions and parameter
text are in Polish, like the rest of the agent-facing surface.

## Install and enable

```bash
# The module is a uv workspace member: plain `uv sync --extra dev` does NOT install it
uv sync --extra dev --all-packages

# Module tests (against the installed distribution; they never reach the network)
uv run pytest modules/saos/tests -v
```

Every installed module is active unless switched off by `LAW_MCP_SOURCE_MODULES`. `-saos` switches this
one off (the package is then never imported), and `-*,saos` runs only this module. A bare `saos` leaves
every other installed module on.

## Searching

`search_saos_judgments(phrase, signature, date_from, date_to, court_type, judgment_type, limit, offset,
include_portal_links, labour_only)`. All parameters are optional.

- `phrase` (up to 500 characters) and `signature` (up to 100) are sent to SAOS.
- `court_type`: `COMMON`, `SUPREME`, `CONSTITUTIONAL_TRIBUNAL`, `NATIONAL_APPEAL_CHAMBER`,
  `ADMINISTRATIVE`. Default: all.
- `judgment_type`: `Wyrok`, `Postanowienie`, `Uchwała`, `Zarządzenie`, `Uzasadnienie`, or the SAOS names
  `SENTENCE`, `DECISION`, `RESOLUTION`, `REGULATION`, `REASONS`.
- The order is fixed: newest judgment first. `date_to` is always sent and clamped to today, because the
  newest-first order of SAOS otherwise starts with records dated in the future (`3013-12-04` was
  recorded). A `date_from` after today is refused.
- `limit` above 100 is cut to 100. Any `offset` works. SAOS pages by page number, so an offset that is
  not a multiple of `limit` reads two SAOS pages and cuts the window out of them.

A judgment and its reasoning are two records of one case with the same signature (`SENTENCE` and
`REASONS`, each with its own date); a search by signature finds both. Each result holds the signature,
court, division and division code, type, date, an excerpt of at most 800 characters, the SAOS address
of the judgment and the court portal address once it was fetched. The ECLI is always empty for SAOS.
The names of judges are never mapped.

### Result sets and `filter_results`

Every non-empty page is stored as a result set. `filter_results` takes its `result_set_id` and narrows
it with `pattern` on `title`, `signature`, `court`, `type`, `division`, `division_code` or `excerpt`,
with `type_equals`, `year_equals`, a date range on `judgment_date`, and `sort_by`. Filters that only
make sense for acts (`status_equals`) are refused with a Polish message. A set that is only a window of
a longer list (a page, or a scan) says so, and an empty narrowing of it is marked inconclusive: it
proves nothing about the judgments outside the window. `list_result_sets` shows the kind of each set.

## Labour judgments

`filter_results` narrows a stored page by division: `field="division_code", pattern="(15|18|21)$"` keeps
the labour divisions of common courts (see the rule below). When a page holds few of them, `labour_only`
scans for them.

`labour_only=true` reads successive SAOS pages of 100 records, at most 5 per call, and keeps the labour
judgments until it holds `limit` of them or the list ends:

- **Rule for common courts** (`court_type` absent or `COMMON`): the division code ends in `15` (labour),
  `21` (labour and social insurance) or `18` (social insurance only, counted as labour on purpose).
  `(15|21)$` leaves the social-insurance-only divisions out. A record without a division code is not
  counted. The rule is one function, `is_labour_division`, and the same pattern is quoted in every
  response (`scan.rule`).
- **Rule for the Supreme Court** (`court_type=SUPREME`): the name of any chamber of the record starts
  with `Izba Pracy`. SAOS stores that chamber as `Izba Pracy, Ubezpieczeń Społecznych i Spraw Publicznych`
  (recorded on 15 of the 100 newest records), so the rule takes **the whole chamber, public-affairs cases
  included**. The prefix also covers the spelling `Izba Pracy i Ubezpieczeń Społecznych`, which no
  recorded SAOS record carries. The Supreme Court data in SAOS ends on 2016-06-22. After a plain search,
  `filter_results` with `field="division", pattern="Izba Pracy"` selects the same records.
- **It is a scan, not a search.** There is no `total_count` and no `page_info` (both are `null`). The
  `scan` object holds the rule, the scanned range, the number of SAOS pages fetched and a cursor.
  `offset` is a position in the SAOS result list, not the index of a hit; pass `scan.next_offset` as the
  `offset` of the next call. When a call stops on `limit` the cursor points right after the last hit,
  so nothing is skipped or repeated. `next_offset` is `null` when the list ended.
- **Failures.** If the first page fails the call fails. If a later page fails, the hits found so far are
  returned with `scan.interrupted=true` and a cursor at the first page not read.
- **Stored set.** The hits are stored as a window (`page` scope), at most `limit` of them. If a lowered
  `LAW_MCP_FILTER_MAX_RECORDS` makes the set too big for `filter_results`, a hint gives the call that
  repeats the scan with a smaller `limit`.
- **Refusals** (Polish messages, before any request): the Constitutional Tribunal, the National Appeal
  Chamber and administrative courts, because SAOS gives their records no division type or chamber to
  classify by; and the combination with `include_portal_links`.

## Reading a judgment

`get_saos_judgment_details(source_id, load_content=true)` loads the whole text of a judgment into the
server's document store under the identifier `SAOS/<source_id>` (the response says it in
`loaded.document_id`). From then on the core tools read it: `read_act_content(eli="SAOS/554697")` gives a
table of contents and, with `section`, pages of one section (`limit`, `offset` as for an act),
`search_in_act` searches it and `list_loaded_documents` lists it. Their responses carry `document_id` and
`document_kind` (`judgment`) next to `eli`; for a judgment `eli` repeats the identifier and is not an ELI.
A loaded judgment lives as long as a loaded act: `LAW_MCP_DOC_STORE_TTL` seconds without use, and the oldest
unused document goes first once `LAW_MCP_DOC_STORE_MAX_DOCUMENTS` are loaded.

- **Text.** A common-court text is HTML and is converted like the text of an act. The Supreme Court text is
  plain text and is kept as written. The anonymisation markers SAOS leaves (`(...)`, initials, `D. P. (1)`)
  stay in the text as they are; only their tag is gone.
- **Sections.** One section per level-2 heading (`WYROK`, `UZASADNIENIE`), and `Treść` for the text before
  the first heading or for a text without headings. A line that starts with `Art.` does not start a section.
- **Reasoning.** For a newer common-court judgment the operative part (`SENTENCE`) and the reasoning
  (`REASONS`) are two SAOS records with one case number. When the loaded sentence has no `UZASADNIENIE`
  heading, the module searches SAOS by the case number and joins the reasons record if there is exactly one
  of the same court. `loaded.reasoning` says what happened:

  | `reasoning` | Meaning |
  |---|---|
  | `in_record` | The text has its own `UZASADNIENIE` section; no further request. |
  | `merged` | The separate reasons record was found and joined as the section `UZASADNIENIE`. |
  | `absent` | No reasoning in the source: the record has none and SAOS has no reasons record of that court. The text ends with a section `[Uwaga serwera] Uzasadnienie` that says so; it is a note of the server, not text of the judgment. |
  | `ambiguous` | Several reasons records of that court; none joined, their `source_id` values are listed. |
  | `reasoning_record` | The loaded record is itself a reasons record. |
  | `not_assessed` | Nothing is claimed: another kind of judgment or court, no case number, a search page too short to be sure, a reasons record whose court cannot be compared with the sentence's (one of them gives no court), or the extra request failed. |

  A cut-short search page that holds no reasons record of that court is `not_assessed`, never `absent`. So is a
  page that holds a reasons record of the same case number but whose court cannot be compared with the
  sentence's: `absent` is a claim that the source has no reasoning, and a record that cannot be placed is not
  evidence of that.

  Only `loaded.reasoning` says where the reasoning is. The text of a judgment is data from SAOS: a section in it
  that reads `[Uwaga serwera] Uzasadnienie` or `UZASADNIENIE` proves nothing, because the court's text can hold
  such a heading too.
- **Personal data.** The module never reads the judges or the court reporters, but the text can hold personal
  data the court left in it, for example the names of court staff. Quote with care.
- **Cost.** The detail is one request; the search by case number and the reasons record make it up to three.
  After a refusal from SAOS (HTTP 401, 403 or 451) no further request is sent in that call, and the
  judgment is stored alone. A 429 (too many requests) is sent a second time by the egress client, after the
  pause SAOS asks for; if that is refused too, the call ends the same way and nothing more is asked.
- **Size.** A judgment detail is read within `LAW_MCP_DOC_STORE_MAX_SIZE_BYTES` (the limit of the document
  store), and a larger answer is refused while it streams in. A reasons record that is too large leaves the
  sentence loaded alone. A text with more than 500 sections is refused.

## Court portal links

The search result carries the SAOS address of each judgment. The address of the judgment in the court's
own portal is only in the judgment detail. `get_saos_judgment_details(source_id)` fetches it for one
judgment; `include_portal_links=true` fetches it for the first 5 results of a search. The detail is not
cached.

The address is data from the court's system. It is only ever passed on as text and never fetched, and
only an `http` or `https` address of at most 2048 characters without whitespace is passed on. The
recorded one is an API address of the court's system, not a page meant for a person.

## Requests per call

| Call | SAOS requests |
|------|---------------|
| search, `offset` a multiple of `limit` | 1 |
| search, any other `offset` | 2 |
| search with `include_portal_links` | the search plus up to 5 details, never more than 6 in all (4 details after a two-request search) |
| `labour_only` | at most 5 |
| `get_saos_judgment_details` | 1 |
| `get_saos_judgment_details` with `load_content` | 1 to 3, never more than 3 (the detail, a search by case number, the reasons record) |

Search pages are cached for `LAW_MCP_CACHE_SEARCH_TTL` seconds under their own parameters, so aligned
calls and a scan that resumes at a cursor reuse pages. If a detail fails, that result has no link and the
counters in `portal_links` say so; the first outage stops the remaining detail requests.

## Pace and who sees your queries

The module asks for at most 2 requests per second with a burst of 4 (lower than the core's default,
because SAOS is a public academic service and a page of 100 records is about 140 kB), and uses the lower
of that and the operator's `LAW_MCP_API_RATE_PER_SECOND` and `LAW_MCP_API_RATE_BURST`. It has its own
circuit breaker and its own `/health` entry, `SAOS`. When SAOS fails, the error names the source and says
that the other sources are unaffected; `search_legal_acts` and the journals keep working.

The module talks to one host, `www.saos.org.pl`. **The `phrase` and the `signature` of every call are
sent to it**, together with the filters and the identification of the module in the `User-Agent`. No
request goes to the Sejm for a SAOS tool.

## What SAOS holds

The coverage was measured on 2026-11-02 and is static text, not a live count:

- common courts: 474 859 judgments, the newest from 2026-10-27;
- Supreme Court: 38 081, the newest from 2016-06-22;
- Constitutional Tribunal: 9 503, the newest from 2015-12-09;
- National Appeal Chamber: 22 168, the newest from 2018-09-06;
- administrative courts: none.

No result in SAOS does not prove that a judgment does not exist. The recordings behind these figures
and the other facts above are in `tests/fixtures/saos/PROVENANCE.md`, with their capture times.

## Terms of use and personal data

SAOS publishes no licence and no terms of use that this project found: the API documentation read on
2026-11-02 contains none, and the terms pages of the service itself were not checked. No permission was
requested or obtained. Do not read this module as a statement that quoting judgments verbatim, or
reusing them for a particular purpose, is permitted; that is for the user to establish. The module
identifies itself honestly (name, version and the operator's contact, `LAW_MCP_CONTACT`, which defaults to the project repository) and keeps to the pace above.

If you intend commercial and/or mass use, verify again that using the service is permitted. The
excerpts are cut from judgments that courts already published with the parties abbreviated, but they
still describe real court cases; the module does not map the names of judges or of court reporters. The
text of a loaded judgment can still hold personal data the court left in it.

## Tests

`uv run pytest modules/saos/tests` runs against recordings of the service, redacted for personal names
(`tests/fixtures/saos/`), against skeletons of real judgment details (the structure of the text with every
letter masked) and against derived lists cut from them. An autouse guard makes any name
lookup of a non-local host fail, so a test that forgot its mock cannot send traffic to SAOS.
