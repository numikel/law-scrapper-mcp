# Provenance of the `sejm/` fixtures

**These files are recordings, not illustrations.** They back the coverage measurement of the two
core publishers (DU, MP) on `api.sejm.gov.pl`, a public service run by a state institution. Do not
hand-edit them and do not extend them with invented records. The facts they pin are asserted in
`tests/unit/test_coverage_fixtures.py`.

## Capture

```
Date:     2026-11-08, 11:10:54 - 11:10:57 UTC (three requests of a 19-request session, sequential, >= 1.5 s apart)
Client:   httpx, follow_redirects=False, TLS verification on, no retries
Headers:  User-Agent: law-scrapper-mcp/4.4.2 (+https://github.com/numikel/law-scrapper-mcp)
          Accept: application/json
Budget:   enforced by the capture script before every send; stop on 403, 429, 5xx or a transport error
```

| File | Request | Status | Bytes (original) | SHA-256 (original) | Captured (UTC) |
|---|---|---|---|---|---|
| `publishers_live.json` | https://api.sejm.gov.pl/eli/acts | 200 | 1 157 | 3efb1cfc68281ed2b7ea9177c3641400dfcbd79dc4288f1aaab42c5bdd2a3304 | 2026-11-08 11:10:54 |
| `search_2026_limit1_du.json` | https://api.sejm.gov.pl/eli/acts/search?publisher=DU&year=2026&limit=1 | 200 | 1 430 | 6a6dc16d3c485e3fc66b84baafe67ba1a3e7c003e0d477ac32786dc08c92a4e9 | 2026-11-08 11:10:55 |
| `search_2026_limit1_mp.json` | https://api.sejm.gov.pl/eli/acts/search?publisher=MP&year=2026&limit=1 | 200 | 1 522 | 4affa8e9285fcd53cc37670ac6c5ebdd2d6bff6fbda1c0885c4aff69595ba0e1 | 2026-11-08 11:10:57 |

The `.json` files are the recorded payloads re-indented for reviewable diffs (`indent=2`,
`ensure_ascii=False`, trailing newline); equivalence was checked at write time as
`json.loads(stored) == json.loads(original)`. The response `Content-Type` was not recorded.

| Fact | Measured |
|---|---|
| `GET acts` | a list of two publishers, each with `code`, `name`, `shortName`, `actsCount`, `years`. DU: 98 051 acts, years 1918-2026 (105 years); MP: 66 835 acts, years 1930-2026 (92 years). The hand-written `../publishers.json` has the same keys |
| `acts/search?publisher=DU&year=2026&limit=1` | `totalCount` 1 465; the one item is DU/2026/1465, `pos` = `totalCount`, `promulgation` 2026-11-05 (publication), `announcementDate` 2026-11-02 (act date) |
| `acts/search?publisher=MP&year=2026&limit=1` | `totalCount` 1 100; the one item is MP/2026/1100, `pos` = `totalCount`, `promulgation` 2026-11-05 |
| Order | on both publishers the first item of the year page is the act with the highest position, as recorded for DU 2024 in `../browse_page.provenance.md` |

## Refreshing these recordings

Refresh by hand when an upstream change is suspected, never on a schedule and never in CI: the
project's `User-Agent`, one request at a time with a pause, no retries, and a budget stated before
the session. Update the date, the numbers and the hashes above in the same commit as the payloads.
