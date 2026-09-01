# Provenance of the default `acts/search` page measurement (D7)

**This file records a measurement, not a fixture.** No payload is stored: the response is
709 437 B, and the recording that tests actually load is `browse_page.json` (`limit=5`).
This file exists so the claim "sending `limit` unconditionally reduces outbound traffic"
rests on a number somebody measured rather than on a direction somebody assumed.

## Capture

```
Date:     2026-09-01
Command:  GET https://api.sejm.gov.pl/eli/acts/search?publisher=DU&year=2024
          (no `limit` parameter — this is the point of the measurement)
Headers:  User-Agent: law-scrapper-mcp/4.0.2 (+https://github.com/numikel/law-scrapper-mcp)
          Accept: application/json
Response: HTTP 200, 709 437 B, count=500, totalCount=1984, 500 items, 27 fields per item
Window:   DU/2024/1984 … DU/2024/1485 (descending by pos, contiguous)
Elapsed:  0.735 s
```

Top-level keys are `count`, `items`, `offset`, `searchQuery`, `totalCount` — the same set
`browse_page.json` carries, so the two measurements describe one endpoint shape.

## What this establishes (decision D7)

| Request | Bytes | Records | Bytes / record |
|---|---|---|---|
| `acts/search?publisher=DU&year=2024` (no `limit`) | 709 437 | 500 | ~1 419 |
| `acts/search?publisher=DU&year=2024&limit=5` (recorded) | 6 452 | 5 | ~1 290 |

1. **The API caps its own default page at 500 records**, not at the whole year: `count=500`
   against `totalCount=1984`. The unbounded default is therefore not the 1 093 220 B
   catastrophe F30 found on `acts/{publisher}/{year}` — it is a 709 437 B one.
2. **500 records is 25× the tool's default `limit` of 20.** A `search_legal_acts()` call
   with no explicit limit downloads and deserialises 500 records to return 20.
3. Sending `limit=20` unconditionally therefore cuts the default request by roughly the
   same factor. The exact figure for a `limit=20` page was **not** measured — that would
   have cost a second request, and the per-record cost above is enough to size the win.

## Refreshing this measurement

Re-run only when upstream is suspected of having changed its default page size, and never
from CI. `api.sejm.gov.pl` belongs to a public institution; the politeness Klaster 8
implements applies to our own probes too. This measurement is one request. Update the date
and every number above in the same commit.
