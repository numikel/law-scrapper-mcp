# Provenance of the `edziennik/` fixtures

**These files are recordings, not illustrations.** They back claims about systems we do not
control — the electronic official journals (e-Dziennik ELI) of the Polish voivodeships and of
some ministries and central offices — so they must keep matching what those systems actually
return. Do not hand-edit them and do not extend them with invented records; build derived
shapes in the test instead. The facts they pin are asserted in
`tests/unit/test_edziennik_fixtures.py`.

## Capture

```
Date:     2026-11-01, 08:10:50 - 08:31:42 UTC (one session, requests strictly sequential, no retries)
Client:   httpx, follow_redirects=True, TLS verification on
Headers:  User-Agent: law-scrapper-mcp/4.4.2 (+https://github.com/numikel/law-scrapper-mcp)
          Accept: application/json (application/pdf for the two PDFs, text/html for the HTML page)
```

Three hosts omit their intermediate certificate and fail verification in a stock `httpx`
client; the two PDFs below come from such hosts (`dziennik.lodzkie.eu` and
`edziennik.malopolska.uw.gov.pl`). They were fetched with verification still on, after adding
the intermediate certificate published by the issuing CA.

| File | Request | Status | Content-Type | Bytes (original) | SHA-256 (original) | Captured (UTC) |
|---|---|---|---|---|---|---|
| `publishers_camel.json` | https://edziennik.gdansk.uw.gov.pl/api/eli/acts | 200 | application/json; charset=utf-8 | 232 | e2ccf79ed22361b5c00afbf2afd9f2fa93206209ac5535175d6e2549363d68ab | 2026-11-01 08:10:50 |
| `publishers_pascal.json` | https://edzienniki.bydgoszcz.uw.gov.pl/api/eli/acts | 200 | application/json; charset=utf-8 | 239 | 9b1ffa3c85f4a6826711e9b4d3cb14741f46ca85171291f2be77b936dbf92716 | 2026-11-01 08:11:12 |
| `search_page_camel.json` | https://edziennik.gdansk.uw.gov.pl/api/eli/search?publisher=POL_WOJ_PM&year=2025&limit=5 | 200 | application/json; charset=utf-8 | 2 788 | 575dab89321b58620e8cfa93be45749ce76d24a9e749f165289cdd90d846395e | 2026-11-01 08:17:01 |
| `search_page_pascal.json` | https://edzienniki.bydgoszcz.uw.gov.pl/api/eli/search?publisher=POL_WOJ_KP&year=2025&limit=2 | 200 | application/json; charset=utf-8 | 1 196 | be678ec1f942ef27618269414c995fd01e9222757670ef3d4a98066e161fdeee | 2026-11-01 08:19:12 |
| `act_detail_camel.json` | https://edziennik.gdansk.uw.gov.pl/api/eli/acts/POL_WOJ_PM/2025/5051 | 200 | application/json; charset=utf-8 | 724 | 7eb8fbac69fc99e2e4d3c9dddf45b0bbbcbbe3d0d669fc292f06a179e890af57 | 2026-11-01 08:19:11 |
| `act_detail_pascal.json` | https://edzienniki.bydgoszcz.uw.gov.pl/api/eli/acts/POL_WOJ_KP/2025/6588 | 200 | application/json; charset=utf-8 | 1 165 | 5017796e75b4872d2fe16c8637091270f3ac8377f148664609206812f429ab9b | 2026-11-01 08:19:12 |
| `references_relation.json` | https://edziennik.gdansk.uw.gov.pl/api/eli/acts/POL_WOJ_PM/2025/5049/references | 200 | application/json; charset=utf-8 | 757 | f4c6b2a9167f1d6ca76d8b7fefff16edd40ff625d68a4caa5025c637f9d319df | 2026-11-01 08:24:15 |
| `unknown_publisher.json` | https://edziennik.kielce.uw.gov.pl/api/eli/acts/POL_WOJ_XX/2025?limit=1 | 200 | application/json; charset=utf-8 | 535 | 76bd16307b3d658bb8d8f02c220a23eaead2181865e124c2e0a0a22c4f7c4853 | 2026-11-01 08:17:48 |
| `text_page1.html` | https://edziennik.lublin.uw.gov.pl/api/eli/acts/POL_WOJ_LB/2025/6385/text.html | 200 | text/html | 1 759 | 6083c821bcfd5b80c30c5e88991724dca4f3d90b0277385445cda007eead0a31 | 2026-11-01 08:23:22 |
| `scan_no_text.pdf` | https://edziennik.malopolska.uw.gov.pl/api/eli/acts/POL_WOJ_MP/1999/1447/text.pdf | 200 | application/pdf | 199 369 | e48a64023f875fa89d08a7948e2a410b5f84e0b38fa316f8dc45810c24a20087 | 2026-11-01 08:30:22 |
| `text_layer.pdf` | https://dziennik.lodzkie.eu/api/eli/acts/POL_WOJ_LD/2009/3522/text.pdf | 200 | application/pdf | 63 499 | 7ee650bc9809e2f313f02d71f8d484406ef368cfb3a1498794a14e5c2442d6b3 | 2026-11-01 08:30:24 |

The eight `.json` files are the recorded payloads re-indented for reviewable diffs
(`indent=2`, `ensure_ascii=False`, trailing newline). Key order is upstream order and the
non-breaking space (U+00A0) in one `ShortName` is preserved. Equivalence to the captured bytes
was checked at write time as `json.loads(stored) == json.loads(original)`. It cannot be
checked later by hash: re-serialising compactly reproduces the original SHA-256 for five of
the eight files, and not for the three whose text carries non-ASCII characters, because the
upstream serialiser escapes them as `\uXXXX`. The test therefore checks the record counts of
the JSON files, and the bytes and SHA-256 of the HTML and the two PDFs, which are stored
unchanged (`.gitattributes` marks `*.pdf` as binary so no checkout rewrites their line endings).

Two files hold names of officials who signed the act. They are public signatures on acts of
an official journal: `text_page1.html` and `text_layer.pdf`.

## What the same session established without storing a body

| Fact | Measured |
|---|---|
| Missing act, `GET /api/eli/acts/POL_WOJ_SK/2025/999999` | **HTTP 204**, empty body, `Cache-Control: no-store` — not 404 |
| `GET /api/eli/acts/POL_WOJ_PM/2025/5051/struct` | HTTP 404, empty body (the endpoint does not exist) |
| `limit=abc` | HTTP 400, `application/problem+json` |
| `GET /api/eli/acts/search` | HTTP 200 with a publisher object, not a search |
| Act with no relations, `.../POL_WOJ_PM/2025/5051/references` | HTTP 200, body `{}` (2 B) |
| Default `/api/eli/search` page (no `limit`) | 0 records, 51 B, `totalCount` still reported |
| `limit=1000` (one host) | 1 000 records, 549 217 B |
| `limit=10000`, `publisher=POL_WOJ_PK&year=2025` | **5 396 records, 3 136 041 B**, 2.2 s — the whole year; the endpoint has no upper bound on a page |
| `/api/eli/acts/{publisher}/{year}` with `limit` / `offset` | both honoured (Sejm ignores them there) |
| Unknown publisher code on `/search` and `/acts/{p}/{y}` | HTTP 200 with the host's own acts (12 hosts) |

Search parameters, base request `GET https://edzienniki.duw.pl/api/eli/search?publisher=POL_WOJ_DS&year=2025&limit=3`
(`totalCount` 5 670):

| Parameter | Behaviour | Evidence |
|---|---|---|
| `year` | honoured | one host: 151 482 without, 9 975 with |
| `publisher` | **ignored** | an unknown or wrong code returns the same `totalCount` as the right one |
| `title`, `type`, `keyword`, `date` | honoured | `title=podatku` 332, `type=Obwieszczenie` 113, `keyword=drogi` 300, `date=2025-06-02` 2 |
| `dateFrom` / `dateTo` | honoured, but on the **act date**, not the publication date | June range 400, records published in September |
| `pubDateFrom` / `pubDateTo` | honoured, on the publication date | 378 |
| `dateEffectFrom` / `dateEffectTo`, `inForce`, `sortBy` / `sortDir` | **silently ignored** | 5 670 = base; same first records |
| `limit`, `offset` | honoured; default page is empty | pages contiguous, no overlap |

HTML versus PDF, measured with `pdfplumber` page by page:

| Act | `text.html` | PDF |
|---|---|---|
| POL_WOJ_PM 2025/5051 | **2 546** characters | 83 pages, **222 755** characters, 1 517 193 B; page 1 = 2 546 |
| POL_WOJ_LB 2025/6385 | **1 419** characters | 2 pages, 2 082 characters; page 1 = 1 419 (this is `text_page1.html`) |
| POL_WOJ_WP 2025/9975 | 1 764 characters | 2 pages, 1 825 characters; page 1 = 1 764 |

So `text.html` is only the first page of the PDF, while the act detail still reports
`textHTML: true`. The stored PDFs are the two ends of the other axis: `scan_no_text.pdf`
(POL_WOJ_MP 1999/1447: 1 page, 0 characters, 14 images) has no text layer, and
`text_layer.pdf` (POL_WOJ_LD 2009/3522: 2 pages, 8 128 characters) has one.

### Ministries and central offices (second session, 2026-11-02, 08:52 - 08:55 UTC)

Ten journals of ministries and central offices run the same engine (the list is taken from the
journals' own registry page). One request each for steps 4 and 5 of the admission criterion
(a `title` filter that changes `totalCount`; an act detail plus its `text.pdf`), 40 requests in
all, with TLS verification on.

| Journal | Host | JSON | `title=zzzxxyy`: `totalCount` | Act detail / `text.pdf` | TLS in a stock client |
|---|---|---|---|---|---|
| MRiRW | edziennik.minrol.gov.pl | camel | 1 153 → 0 | 200 / 200, 1 page | ok |
| MSWiA | edziennik.mswia.gov.pl | **Pascal** | 729 → 0 | 200 / 200, 3 pages | intermediate omitted |
| MZ (ministry) | dziennikmz.mz.gov.pl | camel | 1 649 → 0 | 200 / 200, 3 pages | ok |
| KGP | edziennik.policja.gov.pl | camel | 1 945 → 0 | 200 / 200, 15 pages | ok |
| KGSG | e-dziennik.strazgraniczna.pl | camel | 1 276 → 0 | 200 / 200, 1 page | intermediate omitted |
| CBA | edu.cba.gov.pl | Pascal | 807 → 0 | 200 / 200, 19 pages | ok |
| KNF | dziennikurzedowy.knf.gov.pl | camel | 516 → 0 | 200 / 200, 2 pages | ok |
| UKE | edziennik.uke.gov.pl | Pascal | 946 → 0 | 200 / 200, 2 pages | ok |
| GDDKiA | edziennik.gddkia.gov.pl | **Pascal** | 648 → 0 | 200 / 200, 2 pages | intermediate omitted |
| UOKiK | dziennikurzedowy.uokik.gov.pl | camel | 52 → 0 | 200 / 200, 19 pages | intermediate omitted |

The four hosts with an omitted intermediate were reached with the issuing CA's intermediate
(three downloads from the CA's repository, each signed directly by a root in `certifi`),
with Python's default `VERIFY_X509_PARTIAL_CHAIN` flag cleared so the chain still had to
reach that root. Every `text.pdf` carried a text layer on page 1 (894 to 2 856 characters).
The voivodeship journal of Mazovia (`edziennik.mazowieckie.pl`) did not answer eight requests
over two days (read timeout, connection reset).

## Refreshing these recordings

Refresh by hand when an upstream change is suspected, never on a schedule and never in CI.
These hosts belong to public institutions, and the restraint this project applies to its own
traffic applies to the test traffic too: use the project's `User-Agent`, send requests one at a
time with a pause between them, make no retries, send at most 15 requests to one host, and
never disable TLS verification. Update the date, the numbers and the hashes above in the same
commit as the payloads.

The raw capture log of the sessions is kept outside the repository because it contains session
cookies.
