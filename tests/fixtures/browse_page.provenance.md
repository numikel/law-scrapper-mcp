# Provenance of `browse_page.json`

**This fixture is a recording, not an illustration.** It exists to back a claim about a
system we do not control, so it must keep matching what that system actually returns. Do
not hand-edit it, and do not extend it with invented records — build derived shapes in the
test instead, the way `test_first_page_of_a_large_year_reports_a_next_offset` does.

## Capture

```
Date:     2026-09-01
Command:  GET https://api.sejm.gov.pl/eli/acts/search?publisher=DU&year=2024&limit=5
Headers:  User-Agent: law-scrapper-mcp/4.0.1 (+https://github.com/numikel/law-scrapper-mcp)
Response: HTTP 200, 6 452 B, count=5, totalCount=1984, 5 items, 27 fields per item
```

The stored file is the recorded payload re-indented for reviewable diffs. Nothing else was
changed: key order is upstream order, and the round-trip back to the captured bytes was
asserted at capture time.

## What the same probe established (acceptance criterion 17, finding F30)

Two further requests were made in the same run and are **not** stored — the second because
no test needs it, the third because it is a megabyte:

| Request | Result |
|---|---|
| `acts/search?publisher=DU&year=2024&limit=5` | 6 452 B · `count=5` · `totalCount=1984` · 5 items |
| `acts/search?publisher=DU&year=2024&limit=5&offset=5` | 6 769 B · 5 items · **zero overlap** with page 1 |
| `acts/DU/2024?limit=5&offset=5` | **1 093 220 B · 1984 items** — both parameters ignored |

So, measured rather than assumed:

1. `acts/search` **honours `limit`** — five records requested, five returned.
2. `acts/search` **honours `offset`** — page 1 returned `DU/2024/1984…1980`, page 2 returned
   `DU/2024/1979…1975`. Descending by `pos`, contiguous, no record repeated or skipped.
3. `acts/{publisher}/{year}` **ignores both** — asked for five, answered with the whole
   year. This is the behaviour F30 exists to escape.
4. The ratio at the default page size is **169×** fewer bytes.

## Field contract (acceptance criterion 10)

The search endpoint returns a **superset** of the year endpoint's fields: 27 against 15, and
all 15 are present in the 27. Switching endpoints therefore loses nothing, and the extra 12
do not reach the tool response because `SearchService._format_act` reads a fixed whitelist.

One correction the recording forced, worth keeping in mind when reading older tests: the
hand-written fixture this replaced carried a `dateEffect` key that **neither endpoint
returns**, and omitted 15 keys that both or one of them do. `_format_act` reads
`item.get("dateEffect")`, so `effective_date` has always been `None` in practice. That
predates the endpoint switch and is not a regression — but the invented fixture had been
quietly asserting the opposite.

## Refreshing this recording

Re-run the capture when upstream is suspected of having changed, not on a schedule tied to
CI. `api.sejm.gov.pl` belongs to a public institution and the politeness this whole cluster
implements applies to our own test traffic too: the probe is three requests, run by hand.
Update the date and the numbers above in the same commit as the payload.
