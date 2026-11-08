# Provenance of the `edziennik/` fixtures

**These files are recordings, not illustrations.** They back claims about systems we do not
control — the electronic official journals (e-Dziennik ELI) of the Polish voivodeships and of
some ministries and central offices — so they must keep matching what those systems actually
return. Do not hand-edit them and do not extend them with invented records; build derived
shapes in the test instead. The facts they pin are asserted in
`tests/unit/test_edziennik_fixtures.py`.

## Capture

```
Date:     2026-11-01, 08:10:50 - 08:31:42 UTC (one session, requests sequential, no pause between them)
Client:   httpx, follow_redirects=True, TLS verification on
Headers:  User-Agent: law-scrapper-mcp/4.4.2 (+https://github.com/numikel/law-scrapper-mcp)
          Accept: application/json (application/pdf for the two PDFs, text/html for the HTML page)
```

Three hosts omit their intermediate certificate and fail verification in a stock `httpx`
client; the two PDFs below come from such hosts (`dziennik.lodzkie.eu` and
`edziennik.malopolska.uw.gov.pl`). They were fetched with verification on, after adding the
intermediate certificate published by the issuing CA. That intermediate was downloaded over
plain HTTP and loaded with Python's default `VERIFY_X509_PARTIAL_CHAIN` flag, so it was trusted
as an anchor instead of being checked against a root; the second session below closed this gap.

The first session fell short of the refresh rules at the end of this file in two ways. It sent
requests back to back, up to ten starting within one second. And when
`edziennik.mazowieckie.pl` timed out three times, it tried again: once with an added
`Accept-Language` header and twice with `curl`, always with the project's `User-Agent`. None of
those six requests was answered. The second session paced and budgeted every request.

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
the eight files, and not for the three from the Kujawy-Pomerania host, whose serialiser writes
some characters as `\uXXXX` escapes where the other hosts write raw UTF-8. The test therefore
checks the record counts of the JSON files, and the bytes and SHA-256 of the HTML and the two
PDFs, which are stored unchanged (`.gitattributes` marks this directory `-text` so no checkout
rewrites line endings).

The two date fields mean different things on different endpoints. In `search_page_*.json`,
`promulgation` is the act date and `announcementDate` the publication timestamp. The act detail
of the same act (`act_detail_*.json`: POL_WOJ_PM 2025/5051 and POL_WOJ_KP 2025/6588) has them
the other way round, as the Sejm API does.

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
journals' own registry page). Each journal got three requests for steps 4 and 5 of the
admission criterion (a `title` filter that changes `totalCount`; an act detail plus its
`text.pdf`), 30 in all. The session made 40 requests with TLS verification on: those 30, three
certificate downloads, two to Mazovia and five pages looking for terms of use.

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
(three intermediates from two CAs' repositories, each signed directly by a root in `certifi`),
with Python's default `VERIFY_X509_PARTIAL_CHAIN` flag cleared so the chain still had to
reach that root. Every `text.pdf` carried a text layer on page 1 (894 to 2 856 characters).
The voivodeship journal of Mazovia (`edziennik.mazowieckie.pl`) did not answer eight requests
over two days (read timeout, connection reset).

### Keyword list (third session, 2026-11-03, 19:27 UTC)

One question, three requests to one host (`edziennik.kielce.uw.gov.pl`, camel, TLS ok in a stock
client): what does `keyword=a,b` mean, the form the Sejm path sends for several keywords? The two
keywords come from that host's own keyword dictionary (78 values, recorded in the first
session). Requests were sequential, at least 1.5 s apart, with the project's `User-Agent` and
`Accept: application/json`, redirects off, TLS verification on; the script enforced the budget of
three before each send. All three answered 200 and the bodies are stored unchanged apart from the
re-indenting described above.

| File | Request | Status | Content-Type | Bytes (original) | SHA-256 (original) | Captured (UTC) |
|---|---|---|---|---|---|---|
| `keyword_single_a.json` | https://edziennik.kielce.uw.gov.pl/api/eli/search?year=2025&limit=1&keyword=bud%C5%BCety | 200 | application/json; charset=utf-8 | 48 | 0bf60cc21b4fe8a689f76395d9e46f54bfa45e6d8be3e07b79aea925e95052e5 | 2026-11-03 19:27:33 |
| `keyword_single_b.json` | https://edziennik.kielce.uw.gov.pl/api/eli/search?year=2025&limit=1&keyword=finanse+publiczne | 200 | application/json; charset=utf-8 | 514 | db3fa449bea8c4f1d7aee18ad269a85fb342d981cfbec5ba36b26f23002d2960 | 2026-11-03 19:27:34 |
| `keyword_pair_ab.json` | https://edziennik.kielce.uw.gov.pl/api/eli/search?year=2025&limit=1&keyword=bud%C5%BCety,finanse+publiczne | 200 | application/json; charset=utf-8 | 514 | db3fa449bea8c4f1d7aee18ad269a85fb342d981cfbec5ba36b26f23002d2960 | 2026-11-03 19:27:35 |

| Fact | Measured |
|---|---|
| `keyword=budżety` (a), `year=2025` | `totalCount` 0 |
| `keyword=finanse publiczne` (b), `year=2025` | `totalCount` 1 694 |
| `keyword=budżety,finanse publiczne`, `year=2025` | `totalCount` 1 694; the body is byte-identical to the one for `b` alone |
| Reading | Not an intersection: an AND would be at most 0. Whether the host treats the list as OR or reads only one of the values cannot be told apart from this data, because `a` matched nothing. Either way a comma-joined list does not narrow the result |

### Newest act per journal (fourth session, 2026-11-08, 11:10 - 11:23 UTC)

How fresh is each served journal, and which request tells? Sixteen requests to the twelve served
hosts, inside a 19-request session whose other three went to `api.sejm.gov.pl` (`../sejm/`). Same
rules as the third session: sequential, at least 1.5 s apart, the project's `User-Agent` with the
module suffix (`law-scrapper-mcp-voivodeship-journals/0.0.0`), `Accept: application/json`, redirects
off, TLS verification on, no retries, the budget enforced before each send (15 approved, then 2 and
2 more, each pair approved separately). All answered 200; the bodies are stored re-indented as above.
The response `Content-Type` was not recorded.

| File | Request | Status | Bytes (original) | SHA-256 (original) | Captured (UTC) |
|---|---|---|---|---|---|
| `year_page_2026_ds.json` | https://edzienniki.duw.pl/api/eli/search?year=2026&limit=1 | 200 | 535 | 0de8f69fdbb92ffb907359a470b645f767f2385ab0021dee402107683ff4aa57 | 2026-11-08 11:10:58 |
| `year_page_2026_kp.json` | https://edzienniki.bydgoszcz.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 755 | 33fe2edbf89bd2b8e9621a41842a324aa085ee7133d4865c9e6c468b0ec280ce | 2026-11-08 11:11:00 |
| `year_page_2026_lb.json` | https://edziennik.lublin.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 541 | 27b5d0340f8fd3867bf3686afff7b1dde0c46dfabf164cee54973689beb31870 | 2026-11-08 11:11:01 |
| `year_page_2026_op.json` | https://duwo.opole.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 796 | 79b1d059d7390521104b6f793b29dc6a8fcc2372e5b1d1aff7db41312d1b4c67 | 2026-11-08 11:11:03 |
| `year_page_2026_pk.json` | https://edziennik.rzeszow.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 579 | 26fe20d3654b49b969b01dcbf629d3837a84f5960ecdb8b3e6da166dc3041333 | 2026-11-08 11:11:04 |
| `year_page_2026_pl.json` | https://edziennik.bialystok.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 532 | c42a81d5e4ba26d243192095648352a2daf909d2ffbaeaecc89fcd9a42f9afd3 | 2026-11-08 11:11:06 |
| `year_page_2026_pm.json` | https://edziennik.gdansk.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 534 | 9279eb484c2adf9489cf522272724ab838e5b70e00e3c872c886226796a29be4 | 2026-11-08 11:11:07 |
| `year_page_2026_sl.json` | https://dzienniki.slask.eu/api/eli/search?year=2026&limit=1 | 200 | 822 | 815b2627145048ceecf65b16161969f1a17712fe8c1dae475005f62a890580af | 2026-11-08 11:11:09 |
| `year_page_2026_sk.json` | https://edziennik.kielce.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 565 | 0b93d12056cdf03e4b3e2d08f354d163a8d61f314476b0ff1505aa2eb128dca3 | 2026-11-08 11:11:10 |
| `year_page_2026_wm.json` | https://edzienniki.olsztyn.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 534 | 0b25fb29b106b2a5c53b7b228a782ec126d5b1b4910575765743ca8bc9259f5e | 2026-11-08 11:11:12 |
| `year_page_2026_wp.json` | https://edziennik.poznan.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 754 | 705716bd7d799bba78bc914f42b40975c12701114bb223737cbfcb3fb44b7784 | 2026-11-08 11:11:13 |
| `year_page_2026_zp.json` | https://e-dziennik.szczecin.uw.gov.pl/api/eli/search?year=2026&limit=1 | 200 | 593 | ce795b79ae0d314220fcdb23d67597182d552fbb1992f77035767ca96abf421c | 2026-11-08 11:11:15 |
| `pubdatefrom_only_sk.json` | https://edziennik.kielce.uw.gov.pl/api/eli/search?pubDateFrom=2026-10-09&limit=1 | 200 | 522 | 97786d04abff024c793a63907a6d872a18603afc6938be613e992526dc8b66b4 | 2026-11-08 11:20:18 |
| `pubdatefrom_only_wp.json` | https://edziennik.poznan.uw.gov.pl/api/eli/search?pubDateFrom=2026-10-09&limit=1 | 200 | 544 | cd065d11cc7d41ae08b52c32727c874a6018322e8512a9d0093ed667d72b6813 | 2026-11-08 11:20:21 |
| `act_detail_last_position_sk.json` | https://edziennik.kielce.uw.gov.pl/api/eli/acts/POL_WOJ_SK/2026/3577 | 200 | 847 | 981b56f51431b3c651359a822f432cc193c806df9d729e22c44d8ea656e8ec09 | 2026-11-08 11:23:32 |
| `act_detail_last_position_wp.json` | https://edziennik.poznan.uw.gov.pl/api/eli/acts/POL_WOJ_WP/2026/8206 | 200 | 851 | f40ea8da5156e1a1b18953f060f0e3e5ffec8077b072af60bf3fd71e1473c97e | 2026-11-08 11:23:34 |

| Fact | Measured |
|---|---|
| First item of the 2026 page is the newest | true on 10 hosts: `pos` equals `totalCount` (KP: `pos` 5 381, `totalCount` 5 380) and `announcementDate` is 2026-11-05 or 2026-11-06. **False on two**: SK (`totalCount` 3 577, first item 2026/2412 announced 2026-07-15) and WP (`totalCount` 8 206, first item 2026/5517 announced 2026-07-15). The default order is not newest-first everywhere, and `sortBy`/`sortDir` are ignored (second table above) |
| `pubDateFrom` alone, no `pubDateTo`, no `year` (SK, WP) | **ignored**: `totalCount` is the whole journal (SK 80 489, WP 151 839) and the first item was announced 2026-07-30, before the bound. The range that the second table shows honoured had both bounds and a year, on another host |
| Act at position `totalCount` of the newest year (SK, WP) | exists and is recent: SK 2026/3577 and WP 2026/8206, both published 2026-11-06 (in the act detail `promulgation` is the publication timestamp, as described above). Positions are assigned in publication order, so this is the freshness probe that does not depend on the host's order |
| Q-11 (WP's newest announcement 2026-07-30 in the first session) | an artefact of the order: that session read the first item of an unordered page. WP published through 2026-11-06 |

## Refreshing these recordings

Refresh by hand when an upstream change is suspected, never on a schedule and never in CI.
These hosts belong to public institutions, and the restraint this project applies to its own
traffic applies to the test traffic too: use the project's `User-Agent`, send requests one at a
time with a pause between them, make no retries, send at most 15 requests to one host, and
never disable TLS verification. Stop at the first timeout or reset: that host is done for the
session, and the request is not sent again with other headers or another client. Trust a
missing intermediate only after checking that a root in `certifi` signed it. Update the date,
the numbers and the hashes above in the same commit as the payloads.

The raw capture log of the sessions is kept outside the repository because it contains session
cookies.
