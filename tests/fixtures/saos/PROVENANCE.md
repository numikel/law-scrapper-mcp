# Provenance of the `saos/` fixtures

**These files are recordings, redacted for personal names, not illustrations.** They back claims
about a system we do not control: the public API of SAOS (System Analizy Orzeczeń Sądowych,
`www.saos.org.pl`), an academic service that publishes Polish court judgments. They must keep
matching what that service actually returns. Do not hand-edit them and do not extend them with
invented records; build derived shapes in the test instead. The facts they pin are asserted in
`tests/unit/test_saos_fixtures.py`.

Every file is a search page (an excerpt of at most 800 characters per judgment), a count, an error
body or the court dictionary. No file holds the whole text of a judgment; the four recordings that
do are described further down and are kept outside the repository. Personal names were removed
from every file before it was stored, as described under Redaction. The unredacted recordings are
not in this repository either.

## Capture

```
Round 1:  2026-11-02, 11:27:49 - 11:33:18 UTC, 15 requests: 14 x 200 and one deliberate 400
Round 2:  2026-11-02, 12:20:34 - 12:20:54 UTC, 14 requests, all 200
Client:   httpx 0.28.1, follow_redirects=True, TLS verification on; no request was redirected
Pacing:   sequential, at least 1.5 s between request starts (enforced by the recorder), no retries
Headers:  User-Agent: law-scrapper-mcp/4.4.2 (+https://github.com/numikel/law-scrapper-mcp)
          Accept: application/json, omitted on one request (see the Accept column)
```

The recorder stops the whole run at the first 403, 429, 5xx or transport error and refuses to
start again; none occurred. Two earlier attempts in round 1 failed before any connection was made
(a malformed host name); they sent nothing and do not count. The service answered every request
within 1.7 s. Its responses carry no rate-limit headers.

## Redaction

The stored files are not the captured bytes. A deterministic script, kept outside this repository
together with the raw recordings, reads each captured body and writes the stored file. Running it
again gives identical bytes. The transformation, in full:

1. `items[].judges[].name` is replaced by the string `[redacted]`. `function` and `specialRoles`
   (for example `PRESIDING_JUDGE`) are kept.
2. In every `items[].textContent` (an excerpt of the judgment text) each word of a personal name is
   replaced by the letter `x` repeated to the same length, so the excerpt keeps its length (initials
   become `x.`). A word counts as a name word when
   - it is a word of that item's own `judges[].name`, or
   - it follows a role cue as a capitalised word or an initial, up to four in a row. The cues are
     the judge ranks and words `SSA`, `SSO`, `SSR`, `SSN`, `sędzia`, `przewodniczący`,
     `protokolant`, `sprawozdawca`, the clerk, trainee and assessor words (`sekretarz`, `sekr.`,
     `referendarz`, `asesor`, `stażysta`, `aplikant`), and the prosecutor, counsel and similar
     words (`prokurator`, `adwokat`, `radca`, `pełnomocnik`, `obrońca`, `kurator`, `biegły`,
     `świadek`), or
   - it is one of the literal strings added after reading every excerpt (one string: the name of a
     prosecutor that the cue rule did not reach).
3. Nothing else is changed: dates, court and division names, case numbers, counts, query templates,
   links and keywords are stored as captured.

Redactions per file (all other files contain none):

| File | `judges[].name` replaced | Name words masked in excerpts |
|---|---|---|
| `search_common_newest_unbounded.json` | 8 | 23 |
| `search_common_newest_le_today.json` | 3 | 4 |
| `search_supreme_newest_le_today.json` | 9 | 24 |
| `search_labour_division_viii.json` | 19 | 17 |
| `search_viii_sentence_all_years.json` | 1 | 4 |
| `search_case_number_pair.json` | 2 | 4 |
| `search_newest_constitutional_tribunal.json` | 3 | 10 |
| `search_newest_national_appeal_chamber.json` | 1 | 4 |
| `search_viii_sentence_2013.json` | 1 | 4 |
| `search_viii_sentence_2016.json` | 1 | 4 |
| `search_viii_reasons_2016.json` | 1 | 0 |
| `search_viii_sentence_2019.json` | 3 | 0 |
| `search_viii_reasons_2019.json` | 1 | 0 |
| `search_viii_sentence_2022.json` | 1 | 7 |
| `search_viii_reasons_2022.json` | 1 | 0 |
| `search_viii_sentence_2025.json` | 1 | 4 |
| `search_viii_reasons_2025.json` | 1 | 0 |
What remains in the excerpts by design: the abbreviations the courts themselves publish for
parties (`J. K.`, `(...)`), names of institutions and companies, and names of public offices.
Every excerpt was read in full after redaction, and a scan for name-like words was run over all of
them; nothing name-like was left that could not be classified. The excerpts are cut at 800
characters, so a word at the cut can be incomplete.

Because the files differ from the captured bytes, the table below gives two hashes. The stored
file's SHA-256 is what the test pins. The original's SHA-256 and size are provenance only: the
original is not in the repository, so no test can check them. `.gitattributes` marks this directory
`-text` so no checkout rewrites line endings. The stored files are re-indented for reviewable diffs
(`indent=2`, `ensure_ascii=False`, trailing newline).

| File | Request | Status | Content-Type | Bytes (stored) | SHA-256 (stored file) | Bytes (original) | SHA-256 (original, not in repo) | Captured (UTC) | Accept sent |
|---|---|---|---|---|---|---|---|---|---|
| `search_common_newest_unbounded.json` | https://www.saos.org.pl/api/search/judgments?pageSize=10&courtType=COMMON&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 21 171 | d2a2421ae2e6a1a055815a98c4bea3baacf40d8eb35776570eab26eaef986c77 | 17 072 | 927bea03b0572e5a8b80d61f13def6eb653d75e3686aa9bfe14316da8a61f592 | 2026-11-02 11:27:49 | yes |
| `search_common_newest_le_today.json` | https://www.saos.org.pl/api/search/judgments?pageSize=5&courtType=COMMON&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 12 481 | 0b6a0bf69ef5da58675e0b3caf720734efb6190d535ff3b0bde09f1fc320465b | 10 009 | 8133b3619f454866f3d88318d99628d301d66ac6397cb45a454cc953301be345 | 2026-11-02 11:28:59 | yes |
| `search_supreme_newest_le_today.json` | https://www.saos.org.pl/api/search/judgments?pageSize=3&courtType=SUPREME&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 9 957 | d7619022c605ddf510fd600e15d26458c9cb868fbcc7c8c136a031be786deb54 | 7 581 | 1a8a7ebe6cc558660c25174c972a34d6fd5d18fb5fd7651aa832b14a0fa826c0 | 2026-11-02 11:29:19 | no |
| `search_division_name_pracy_empty.json` | https://www.saos.org.pl/api/search/judgments?pageSize=10&courtType=COMMON&ccDivisionName=Pracy&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 3 716 | 68a326b78a66ad8514c2a5eacec341e598040dcf7f229a9224a1ecfeae26308d | 2 771 | 010bc6d7feeb01e3423c01cf721f879e1681247351c488d2a44004dd376c012d | 2026-11-02 11:29:26 | yes |
| `search_division_name_unnumbered_empty.json` | https://www.saos.org.pl/api/search/judgments?pageSize=10&courtType=COMMON&ccDivisionName=Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 3 810 | c5350bccab305b3b66f67706dc4f5f5a983f66ce7ebba29fa82298fcf0b25272 | 2 865 | 23f0f58774993995f47ca95c610ea03dcacea0493c5f84729f6ad185ce2bd14b | 2026-11-02 11:29:48 | yes |
| `search_labour_division_viii.json` | https://www.saos.org.pl/api/search/judgments?pageSize=20&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 38 936 | 755a8b8866ea9134d3a17fec148b2eb1eeb31e342f313b5b34f80518b67bffeb | 31 471 | 263e5d18d19f0837ae38b97a543fe69c20e7294dc106414765fa2b2aea9f950a | 2026-11-02 11:30:24 | yes |
| `search_viii_sentence_all_years.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=SENTENCE&judgmentDateTo=2026-11-02 | 200 | application/json;charset=UTF-8 | 5 963 | f83c5ab5ec976f0dbd67cd9e68e5a0d8e85aaaaa30d24ed28981091df1f5bb99 | 4 645 | 06d8b868ddd937b528178269387d42801d5d2b0457cb6bb634e26b61e1643e08 | 2026-11-02 11:30:43 | yes |
| `search_viii_reasons_all_years.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=REASONS&judgmentDateTo=2026-11-02 | 200 | application/json;charset=UTF-8 | 5 761 | af6150c2bdfed841eab0d5d50f92fbf9166a446f997e9a8537e3afd65a780def | 4 530 | f6a84f6eef9dadb8e8a3625751f0d99e6af00b1ba79a3f69249e816fc5243366 | 2026-11-02 11:30:44 | yes |
| `search_by_law_journal_entry.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&lawJournalEntryCode=1964%2F296&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 5 642 | 081eef264fa503c105f938f906cd0aac56c99dbe6f4fff2ea45534841489e4bf | 4 402 | 0e60b2a1a01d2517b0f5a775614b6be120b39b355222121c13f4360ae25b1b8d | 2026-11-02 11:31:46 | yes |
| `error_page_size_101.json` | https://www.saos.org.pl/api/search/judgments?pageSize=101&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentDateFrom=2026-10-20&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 400 | application/json;charset=UTF-8 | 187 | 2239975ce013a40c1a1a8b2f9612c42e2dbaebb3cc3beaa380ce28d9895d3cc2 | 154 | d14547ff0934985b43804461692fed6494d38e66d34ef15a3dd6736093984d48 | 2026-11-02 11:31:56 | yes |
| `common_courts_page0.json` | https://www.saos.org.pl/api/dump/commonCourts?pageSize=100&pageNumber=0 | 200 | application/json;charset=UTF-8 | 125 137 | 0be78fe84414595da27a90a19a92ff0df7813774be3e3ea7dbcd9447ff8cabf1 | 72 768 | d94e40bfd9833671d0672da190d26a3bcd613a2aab805c44f0e13a82c84f06c3 | 2026-11-02 11:32:55 | yes |
| `search_case_number_pair.json` | https://www.saos.org.pl/api/search/judgments?pageSize=10&caseNumber=VIII+U+2924%2F25 | 200 | application/json;charset=UTF-8 | 7 234 | 648415a2d4383abf1c2b8dcde2a9cf9f7151aee0b7756561f6ebeedfe6d87833 | 5 643 | 37871e86ab8375bb99e60247c6a218f57a77954d26431df9ad82bb47ec9eb11c | 2026-11-02 12:20:34 | yes |
| `search_newest_constitutional_tribunal.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=CONSTITUTIONAL_TRIBUNAL&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 5 568 | da50c75e6f677ddd4579f92d7771fb3ae060668582ae09595f6285fe7ddb2847 | 4 293 | 7b115326472b5a60d4eb5acab8334023edb75df2da56403261e34e0d5df49c16 | 2026-11-02 12:20:36 | yes |
| `search_newest_national_appeal_chamber.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=NATIONAL_APPEAL_CHAMBER&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 5 322 | fb33448c6e95f907d5f1ea94bff5bc151f6323565f0483412b5b2d3b9040ba60 | 4 148 | 729e5297334b20d39d95c902c37a4bb451f98b29090e0280f8cbf8393b71dc64 | 2026-11-02 12:20:37 | yes |
| `search_newest_administrative.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=ADMINISTRATIVE&judgmentDateTo=2026-11-02&sortingField=JUDGMENT_DATE&sortingDirection=DESC | 200 | application/json;charset=UTF-8 | 3 706 | c5e18b8f2bff9ce4ac715ca8d78be29b45a550e684f532e37a87816a27157edd | 2 761 | d636f6de28a79677211e15d3f0d895bce3388e24c8a73df167fdaade607112f9 | 2026-11-02 12:20:39 | yes |
| `search_viii_sentence_2013.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=SENTENCE&judgmentDateFrom=2013-01-01&judgmentDateTo=2013-12-31 | 200 | application/json;charset=UTF-8 | 6 027 | b751f749a7e0506c643b0501a66697aa37b3ca6da009f43162f4e4198d2fddff | 4 709 | 8c38fdc967bc9ce032e679938a49b0fed39f4cf8292b8081db24bb5e706604c7 | 2026-11-02 12:20:40 | yes |
| `search_viii_reasons_2013.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=REASONS&judgmentDateFrom=2013-01-01&judgmentDateTo=2013-12-31 | 200 | application/json;charset=UTF-8 | 3 899 | 42f9b57b1eb894464803ec3cfbf2739e34c1e734e8dbe1a1d8e2cef6bf2d53ee | 2 938 | b82f9b62dbcb3846abfd5b29ea8d2c90596ee2d0aab3f3e8adbbad2ff14e71ae | 2026-11-02 12:20:42 | yes |
| `search_viii_sentence_2016.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=SENTENCE&judgmentDateFrom=2016-01-01&judgmentDateTo=2016-12-31 | 200 | application/json;charset=UTF-8 | 5 995 | ff3d52891969fb3a74cc56edf55119532df318d8de84c9c6a1c301df94684019 | 4 689 | 01711a7bf0589f4fc6aa72b0b48ede459e71fb33eee2b2b4849464f415e46da0 | 2026-11-02 12:20:43 | yes |
| `search_viii_reasons_2016.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=REASONS&judgmentDateFrom=2016-01-01&judgmentDateTo=2016-12-31 | 200 | application/json;charset=UTF-8 | 6 006 | 1a4c8fa172771c213cd7f5a43ce21d8ff2c8f82058317413e950f6d094d76382 | 4 684 | 0c2c083740e759584a04b6ce70a0e31fbbaea743bd72aa445ec9fb8ee79ff332 | 2026-11-02 12:20:45 | yes |
| `search_viii_sentence_2019.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=SENTENCE&judgmentDateFrom=2019-01-01&judgmentDateTo=2019-12-31 | 200 | application/json;charset=UTF-8 | 5 398 | 55cf49065940307be7957de072c2174d98ca13e6337a2856937af57a5cb1be28 | 3 995 | e01ad441330f1d735827ba761290390f9bd276dee5efa82888adc86b39b4b611 | 2026-11-02 12:20:46 | yes |
| `search_viii_reasons_2019.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=REASONS&judgmentDateFrom=2019-01-01&judgmentDateTo=2019-12-31 | 200 | application/json;charset=UTF-8 | 6 011 | 577c4225e7237c950070a88a9cec03b63bec6b45001e6a96adf174c9d1f80126 | 4 678 | 6cda9ad2c23d8c562286fe5554a6496bc01e4e895af66a4960072462f5c039b3 | 2026-11-02 12:20:48 | yes |
| `search_viii_sentence_2022.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=SENTENCE&judgmentDateFrom=2022-01-01&judgmentDateTo=2022-12-31 | 200 | application/json;charset=UTF-8 | 6 045 | cb4fcd8af14ef3fbdb8c2e1cfc9371189c9d12b9a8d82dcef59bc23868d47385 | 4 718 | 0718c326b4782cafb96daea6e1afcdbf8b6aa5b59c8ab4ecaf4dc00e1f295375 | 2026-11-02 12:20:49 | yes |
| `search_viii_reasons_2022.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=REASONS&judgmentDateFrom=2022-01-01&judgmentDateTo=2022-12-31 | 200 | application/json;charset=UTF-8 | 6 017 | d194a6c6f03b298db1108812aec7d64241aa3eef14d16f6b4862129689267b05 | 4 690 | d7c5ed32d48206a041363ba4cf020091072ba46a0920b690cb3c5c44425d1b81 | 2026-11-02 12:20:51 | yes |
| `search_viii_sentence_2025.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=SENTENCE&judgmentDateFrom=2025-01-01&judgmentDateTo=2025-12-31 | 200 | application/json;charset=UTF-8 | 6 041 | 046736c449a94909403e17d66b8c65a34ebeedd8d01a49f739cdc44dee8c7ae1 | 4 714 | 77e3dac83da4cbe9285138eabf25fecf0fdbdef80090dbae2a06313ed88e3b79 | 2026-11-02 12:20:52 | yes |
| `search_viii_reasons_2025.json` | https://www.saos.org.pl/api/search/judgments?pageSize=1&courtType=COMMON&ccDivisionName=VIII+Wydzia%C5%82+Pracy+i+Ubezpiecze%C5%84+Spo%C5%82ecznych&judgmentTypes=REASONS&judgmentDateFrom=2025-01-01&judgmentDateTo=2025-12-31 | 200 | application/json;charset=UTF-8 | 6 042 | 00689d67624d8fc91fc3c80b06f14797d4f2d2e4c6b90e27dac6a4c588c5816f | 4 715 | 7039f22eb6ee3dfba7a0fcada21423290250ceeb92232b86b4ec8a4915b3ff16 | 2026-11-02 12:20:54 | yes |
## Recorded, kept private

Four detail recordings hold the whole text of a judgment record (and, for three of them, the names
of court staff who published it). They are not in this repository, redacted or not. Their facts,
taken from the bodies at capture time, are pinned here as `key=value` pairs and asserted by the
test, so a refreshed recording that changes a fact has to change this table too.

| Recording | Request | Status | Content-Type | Bytes (original) | SHA-256 (original) | Captured (UTC) | Facts |
|---|---|---|---|---|---|---|---|
| `detail_labour_sentence_split` | https://www.saos.org.pl/api/judgments/554697 | 200 | application/json;charset=UTF-8 | 3 574 | 49a9d3dd2092dd2f8dd4eca56e8cf2ac2ab6c1d9987ebe9ff48e4dd6fd936f50 | 2026-11-02 11:30:53 | judgmentType=SENTENCE; courtType=COMMON; caseNumber=VIII U 2924/25; divisionId=1023; divisionType=Pracy i Ubezpieczeń Społecznych; textChars=1995; h2=1; uzasadnienieWords=0; anonBlocks=8; cssClasses=anon-block; referencedRegulations=0; regsWithYearAndEntry=0; judgmentUrlScheme=https; isapLinks=0; sourceCode=COMMON_COURT; judgmentDate=2026-10-13 |
| `detail_labour_reasons` | https://www.saos.org.pl/api/judgments/554698 | 200 | application/json;charset=UTF-8 | 61 970 | 2e21faab297d09a5be75eeea622b4f7fe990d100ffec9ee51e8ebd79fb6387ae | 2026-11-02 11:30:55 | judgmentType=REASONS; courtType=COMMON; caseNumber=VIII U 2924/25; divisionId=1023; divisionType=Pracy i Ubezpieczeń Społecznych; textChars=55636; h2=1; uzasadnienieWords=1; anonBlocks=107; cssClasses=anon-block; referencedRegulations=5; regsWithYearAndEntry=5; judgmentUrlScheme=https; isapLinks=11; sourceCode=COMMON_COURT; judgmentDate=2026-10-16 |
| `detail_sentence_with_reasoning` | https://www.saos.org.pl/api/judgments/12510 | 200 | application/json;charset=UTF-8 | 31 798 | c11ca55ead51ee5174523b6940ce3361ab0d6cf934f5fa2b14de259b887d96f5 | 2026-11-02 11:32:25 | judgmentType=SENTENCE; courtType=COMMON; caseNumber=VIII U 177/13; divisionId=442; divisionType=Pracy i Ubezpieczeń Społecznych; textChars=27671; h2=2; uzasadnienieWords=2; anonBlocks=62; cssClasses=anon-block; referencedRegulations=3; regsWithYearAndEntry=3; judgmentUrlScheme=http; isapLinks=5; sourceCode=COMMON_COURT; judgmentDate=2013-05-28 |
| `detail_supreme` | https://www.saos.org.pl/api/judgments/245360 | 200 | application/json;charset=UTF-8 | 9 244 | f96c4bedd738c80b77f8ca99e5ef703cd5cf495464e2cc57112d3d417bbd28c9 | 2026-11-02 11:33:18 | judgmentType=SENTENCE; courtType=SUPREME; caseNumber=III KK 195/16; divisionId=13; divisionType=none; textChars=6689; h2=0; uzasadnienieWords=1; anonBlocks=0; cssClasses=none; referencedRegulations=2; regsWithYearAndEntry=2; judgmentUrlScheme=http; isapLinks=0; sourceCode=SUPREME_COURT; judgmentDate=2016-06-22 |
How to read the facts: `h2` counts `<h2>` headings in the HTML text, `anonBlocks` counts
`<span class="anon-block">` markers, `isapLinks` counts links to the ISAP legal database of the
form `WDU` followed by 11 digits, `regsWithYearAndEntry` counts cited provisions that carry a
numeric journal year and entry. `detail_labour_sentence_split` and `detail_labour_reasons` are the
two records of one case; `detail_sentence_with_reasoning` is a 2013 judgment that carries its
reasoning inside the same record.

## What the recordings establish

Numbers below are taken from the `info.totalResults` of the stored files.

| Fact | Measured |
|---|---|
| All common-court judgments, no upper date bound | 474 863 |
| The same with `judgmentDateTo=2026-11-02` | 474 859: four records are dated after the capture day (the first page of the unbounded request shows them as `3013-12-04`, `2101-04-14`, `2026-12-16`, `2026-11-28`) |
| Newest common-court judgment up to 2026-11-02 | 2026-10-27 |
| Supreme Court judgments up to 2026-11-02 | 38 081; newest 2016-06-22 |
| Constitutional Tribunal | 9 503; newest 2015-12-09 |
| National Appeal Chamber | 22 168; newest 2018-09-06 |
| Administrative courts (`courtType=ADMINISTRATIVE`) | 0 records |
| Division `VIII Wydział Pracy i Ubezpieczeń Społecznych`, all courts, all years | 26 891 records: 15 626 `SENTENCE`, 6 750 `REASONS` |
| `ccDivisionName=Pracy` and `ccDivisionName=Wydział Pracy i Ubezpieczeń Społecznych` | 0 results each: the filter matches the whole name, numeral included |
| `lawJournalEntryCode=1964/296` (Code of Civil Procedure), common courts | 317 900 |
| `caseNumber=VIII U 2924/25` | 2 records: the `SENTENCE` and the `REASONS` of one case |
| `pageSize=101` | HTTP 400, `{"error": {..., "propertyName": "pageSize", "message": "parameter 'pageSize' : may not be greater than 100"}}` |

Records of division VIII by calendar year of their own `judgmentDate`, `pageSize=1`:

| Year | `SENTENCE` | `REASONS` |
|---|---|---|
| 2013 | 202 | 0 |
| 2016 | 2 056 | 490 |
| 2019 | 1 466 | 600 |
| 2022 | 1 232 | 600 |
| 2025 | 695 | 722 |

A `REASONS` record has a date of its own, usually later than the `SENTENCE` of the same case, so
these counts are not the share of judgments that have a reasoning.

Observed only in the private recordings: `source.judgmentUrl` (the court portal address) exists
only in the judgment detail and not in search results; it is an API address of the court portal,
`http://` in a 2013 record and `https://` in a 2026 one; `division.type` (`Pracy i Ubezpieczeń
Społecznych`) is in the detail and not in search results, where only `division.code` is.

Where the documentation and the service disagree:

| Documentation | Service |
|---|---|
| `pageSize` range 10-100 | 1..100 accepted (pages of 1, 3, 5, 10 and 20 were recorded), 101 refused |
| `courtType` values `APPEAL`, `REGIONAL`, `DISTRICT` | `courtType` is `COMMON`, `SUPREME`, `CONSTITUTIONAL_TRIBUNAL`, `NATIONAL_APPEAL_CHAMBER` or `ADMINISTRATIVE`; the three listed are `ccCourtType` |

## Personal data and terms of use

The service publishes judgments with the parties already abbreviated by the courts, and with the
names of judges and court staff. The stored files contain neither the judges' names nor the names
of court staff, prosecutors or counsel found in the excerpts (see Redaction). The scan for contact
data (e-mail addresses, phone numbers, street addresses, postal codes, tax and registry numbers,
11-digit numbers) found nothing in the stored files; the test repeats the e-mail and phone check.
A scan is not a guarantee, and the excerpts still hold other details of real court cases. The
service's terms of use and licence were not checked: the API documentation read for this recording
does not contain them.

## Refreshing these recordings

Refresh by hand when an upstream change is suspected, never on a schedule and never in CI. SAOS is
an academic service, and the restraint this project applies to its own traffic applies to the test
traffic too: use the project's `User-Agent`, send requests one at a time at least 1.5 s apart, make
no retries, send at most 15 requests in a session, never disable TLS verification and never present
the client as a browser. Stop at the first 403, 429, 5xx or timeout: the session is over, and the
request is not sent again with other headers or another client. A refreshed recording must go
through the same redaction before it is stored: no raw body is ever committed. Update the date, the
numbers and the hashes above in the same commit as the payloads. When a count changes (the service
keeps adding judgments) update the table above and the test's expectations derived from it together.

The raw capture log of the sessions and the unredacted recordings are kept outside the repository.
