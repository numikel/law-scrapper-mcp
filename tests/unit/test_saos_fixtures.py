"""Facts about the SAOS case-law API, pinned to the recordings in `fixtures/saos/`.

SAOS (`www.saos.org.pl`) is an academic service with behaviour its documentation gets partly
wrong: a date sort that lists impossible dates first, a division filter that matches the whole
name, judgments stored as two records (operative part and reasoning), a court dictionary whose
codes encode the division type, and a page-size limit that differs from the documented one. Each
test states one such fact and derives its expectation from the recording or from the request
URL in the provenance table, so a refreshed recording that changes the fact turns the test red
instead of silently invalidating an assumption a later change builds on.

Where the recordings came from, and which four recordings are kept outside the repository because
they hold a whole judgment text, is in `tests/fixtures/saos/PROVENANCE.md`. No test here touches
the network.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parents[1] / "fixtures" / "saos"
PROVENANCE = FIXTURES / "PROVENANCE.md"
SNIPPET = 800
YEARS = (2013, 2016, 2019, 2022, 2025)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\d)(?:\+48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?!\d)")

UNBOUNDED = "search_common_newest_unbounded.json"
BOUNDED = "search_common_newest_le_today.json"
SUPREME = "search_supreme_newest_le_today.json"
EMPTY_SUBSTRING = "search_division_name_pracy_empty.json"
EMPTY_UNNUMBERED = "search_division_name_unnumbered_empty.json"
DIVISION_VIII = "search_labour_division_viii.json"
SENTENCES_ALL = "search_viii_sentence_all_years.json"
REASONS_ALL = "search_viii_reasons_all_years.json"
LAW_JOURNAL = "search_by_law_journal_entry.json"
CASE_NUMBER = "search_case_number_pair.json"
PAGE_SIZE_ERROR = "error_page_size_101.json"
COURTS = "common_courts_page0.json"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def provenance_text() -> str:
    return PROVENANCE.read_text(encoding="utf-8")


def table_cells(line: str) -> list[str]:
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]


def provenance_rows() -> dict[str, list[str]]:
    """Capture-table rows keyed by fixture file name.

    Only ten-cell rows whose first cell is a fixture file name count, so the other tables in the
    same document (redactions, private recordings, measurements) are not mistaken for fixtures.
    """
    rows: dict[str, list[str]] = {}
    for line in provenance_text().splitlines():
        if not line.startswith("| `"):
            continue
        cells = table_cells(line)
        if len(cells) == 10 and re.fullmatch(r"[a-z0-9_]+\.json", cells[0]):
            rows[cells[0]] = cells
    return rows


def redaction_rows() -> dict[str, tuple[int, int]]:
    """Per file: judge names replaced and name words masked, from the Redaction table."""
    out: dict[str, tuple[int, int]] = {}
    for line in provenance_text().splitlines():
        if not line.startswith("| `"):
            continue
        cells = table_cells(line)
        if len(cells) == 3 and re.fullmatch(r"[a-z0-9_]+\.json", cells[0]):
            out[cells[0]] = (int(cells[1]), int(cells[2]))
    return out


def private_facts() -> dict[str, dict[str, str]]:
    """Facts of the recordings kept outside the repository, keyed by recording name."""
    out: dict[str, dict[str, str]] = {}
    for line in provenance_text().splitlines():
        if not line.startswith("| `detail_"):
            continue
        cells = table_cells(line)
        out[cells[0]] = dict(pair.split("=", 1) for pair in cells[7].split("; "))
    return out


def request_params(name: str) -> dict[str, str]:
    """Query parameters of the request recorded for a fixture, from its provenance row."""
    query = parse_qs(urlparse(provenance_rows()[name][1]).query)
    return {key: values[0] for key, values in query.items()}


def captured_day(name: str) -> str:
    return provenance_rows()[name][8][:10]


def total(name: str) -> int:
    return int(load_json(name)["info"]["totalResults"])


def thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def test_every_fixture_has_a_provenance_row_and_every_row_has_a_fixture() -> None:
    on_disk = {p.name for p in FIXTURES.iterdir() if p.name != "PROVENANCE.md"}

    assert on_disk
    assert on_disk == set(provenance_rows())


def test_every_stored_file_matches_the_hash_recorded_for_it() -> None:
    """The stored (redacted) bytes are pinned; the original's hash is provenance only and cannot be checked."""
    for name, cells in provenance_rows().items():
        data = (FIXTURES / name).read_bytes()

        assert len(data) == int(re.sub(r"\D", "", cells[4])), name
        assert hashlib.sha256(data).hexdigest() == cells[5], name
        assert re.fullmatch(r"[0-9a-f]{64}", cells[7]), name


def test_search_pages_carry_an_excerpt_and_never_the_source_block() -> None:
    """Portal link, full text, provisions and division type are in the detail, not in a search page."""
    lengths: list[int] = []
    for name in provenance_rows():
        if not name.startswith("search_"):
            continue
        page = load_json(name)

        assert set(page) == {"links", "items", "queryTemplate", "info"}, name
        for item in page["items"]:
            assert "source" not in item and "referencedRegulations" not in item, name
            assert len(item["textContent"]) <= SNIPPET, name
            lengths.append(len(item["textContent"]))
    assert lengths
    assert SNIPPET in lengths


def test_descending_date_sort_without_an_upper_bound_lists_impossible_dates_first() -> None:
    """Typos in the source (`3013-12-04`) and future dates sort ahead of every real judgment."""
    items = load_json(UNBOUNDED)["items"]
    later = [item["judgmentDate"] for item in items if item["judgmentDate"] > captured_day(UNBOUNDED)]

    assert later
    assert [item["judgmentDate"] for item in items[: len(later)]] == later
    assert total(UNBOUNDED) - total(BOUNDED) == len(later)


def test_an_upper_date_bound_gives_the_real_newest_judgment() -> None:
    items = load_json(BOUNDED)["items"]
    dates = [item["judgmentDate"] for item in items]

    assert dates
    assert all(date <= request_params(BOUNDED)["judgmentDateTo"] for date in dates)
    assert dates == sorted(dates, reverse=True)
    assert dates[0] <= captured_day(BOUNDED)


def test_supreme_court_records_have_a_different_shape_and_were_fetched_without_accept() -> None:
    items = load_json(SUPREME)["items"]

    assert items
    assert all(item["courtType"] == "SUPREME" for item in items)
    assert all("code" not in item["division"] for item in items)
    assert all("personnelType" in item and "judgmentForm" in item for item in items)
    assert items[0]["judgmentDate"] == max(item["judgmentDate"] for item in items)
    assert {name for name, cells in provenance_rows().items() if cells[9] == "no"} == {SUPREME}


def test_other_court_types_are_recorded_with_their_newest_date_or_with_no_records() -> None:
    tribunal = load_json("search_newest_constitutional_tribunal.json")
    chamber = load_json("search_newest_national_appeal_chamber.json")
    administrative = load_json("search_newest_administrative.json")

    assert [item["courtType"] for item in tribunal["items"]] == ["CONSTITUTIONAL_TRIBUNAL"]
    assert [item["courtType"] for item in chamber["items"]] == ["NATIONAL_APPEAL_CHAMBER"]
    assert administrative["items"] == []
    assert administrative["info"]["totalResults"] == 0
    assert administrative["queryTemplate"]["courtType"]["value"] == "ADMINISTRATIVE"


def test_division_name_filter_matches_the_whole_name_including_the_numeral() -> None:
    exact_name = request_params(DIVISION_VIII)["ccDivisionName"]

    assert total(DIVISION_VIII) > 0
    for name in (EMPTY_SUBSTRING, EMPTY_UNNUMBERED):
        page = load_json(name)
        asked = request_params(name)["ccDivisionName"]

        assert page["items"] == [], name
        assert page["info"]["totalResults"] == 0, name
        assert [link["rel"] for link in page["links"]] == ["self"], name
        assert page["queryTemplate"]["ccDivisionName"] == asked, name
        assert asked in exact_name and asked != exact_name, name


def test_a_labour_division_page_holds_sentences_and_reasons_of_the_same_cases() -> None:
    page = load_json(DIVISION_VIII)
    exact_name = request_params(DIVISION_VIII)["ccDivisionName"]
    by_case: dict[tuple[int, str], dict[str, int]] = collections.defaultdict(dict)
    for item in page["items"]:
        assert item["division"]["name"] == exact_name
        assert item["division"]["code"].endswith("21")
        key = (item["division"]["court"]["id"], item["courtCases"][0]["caseNumber"])
        by_case[key][item["judgmentType"]] = len(item["textContent"])

    pairs = {key: sizes for key, sizes in by_case.items() if {"SENTENCE", "REASONS"} <= set(sizes)}

    assert {kind for sizes in by_case.values() for kind in sizes} >= {"SENTENCE", "REASONS"}
    assert pairs
    assert any(sizes["SENTENCE"] < SNIPPET for sizes in pairs.values())


def test_sentences_and_reasons_are_counted_separately_in_one_division() -> None:
    exact_name = request_params(DIVISION_VIII)["ccDivisionName"]
    for name, kind in ((SENTENCES_ALL, "SENTENCE"), (REASONS_ALL, "REASONS")):
        page = load_json(name)

        assert page["queryTemplate"]["judgmentTypes"]["value"] == [kind], name
        assert request_params(name)["ccDivisionName"] == exact_name, name
        assert [item["judgmentType"] for item in page["items"]] == [kind], name
    assert 0 < total(REASONS_ALL) < total(SENTENCES_ALL)
    assert total(SENTENCES_ALL) + total(REASONS_ALL) < total(DIVISION_VIII)


def test_reasons_records_appear_after_2013_and_grow_against_sentences() -> None:
    sentences = {year: total(f"search_viii_sentence_{year}.json") for year in YEARS}
    reasons = {year: total(f"search_viii_reasons_{year}.json") for year in YEARS}
    ratios = [reasons[year] / sentences[year] for year in YEARS]

    for year in YEARS:
        assert request_params(f"search_viii_reasons_{year}.json")["judgmentDateFrom"] == f"{year}-01-01"
        assert request_params(f"search_viii_reasons_{year}.json")["judgmentDateTo"] == f"{year}-12-31"
    assert sentences[2013] > 0
    assert reasons[2013] == 0
    assert ratios == sorted(ratios)
    assert reasons[2025] > sentences[2025]


def test_a_case_number_finds_the_sentence_and_the_reasons_of_one_case() -> None:
    page = load_json(CASE_NUMBER)
    items = page["items"]
    sentence = next(item for item in items if item["judgmentType"] == "SENTENCE")
    reasons = next(item for item in items if item["judgmentType"] == "REASONS")

    assert page["info"]["totalResults"] == len(items) == 2
    assert {item["courtCases"][0]["caseNumber"] for item in items} == {request_params(CASE_NUMBER)["caseNumber"]}
    assert sentence["division"]["court"]["id"] == reasons["division"]["court"]["id"]
    assert reasons["judgmentDate"] > sentence["judgmentDate"]


def test_the_law_journal_filter_is_keyed_on_year_and_position() -> None:
    page = load_json(LAW_JOURNAL)
    asked = request_params(LAW_JOURNAL)["lawJournalEntryCode"]

    assert re.fullmatch(r"\d{4}/\d+", asked)
    assert page["queryTemplate"]["lawJournalEntryCode"]["value"] == asked
    assert page["items"]
    assert total(LAW_JOURNAL) > len(page["items"])


def test_a_page_size_above_100_is_refused_with_a_json_error() -> None:
    body = load_json(PAGE_SIZE_ERROR)

    assert set(body) == {"error"}
    assert body["error"]["httpStatus"] == "400"
    assert body["error"]["propertyName"] == "pageSize"
    assert "100" in body["error"]["message"]
    assert provenance_rows()[PAGE_SIZE_ERROR][2] == "400"
    assert {cells[2] for name, cells in provenance_rows().items() if name != PAGE_SIZE_ERROR} == {"200"}


def test_division_codes_in_the_court_dictionary_encode_the_division_type() -> None:
    """Labour divisions end in 15 (`Pracy`) or 21 (`Pracy i Ubezpieczeń Społecznych`), with one stray record."""
    page = load_json(COURTS)
    courts = page["items"]
    divisions = [division for court in courts for division in court["divisions"]]
    labour = [division for division in divisions if "Pracy" in division["name"]]
    social_insurance = [division for division in divisions if division["code"].endswith("18")]

    assert len(courts) == int(request_params(COURTS)["pageSize"])
    assert "next" in {link["rel"] for link in page["links"]}
    assert {court["type"] for court in courts} == {"APPEAL", "REGIONAL", "DISTRICT"}
    assert labour
    assert {division["type"] for division in labour} == {"Pracy", "Pracy i Ubezpieczeń Społecznych"}
    assert {division["code"][-2:] for division in labour} == {"15", "21"}
    assert {
        division["id"] for division in labour if (division["type"] == "Pracy") != division["code"].endswith("15")
    } == {268}
    assert len({division["name"] for division in labour}) >= 10
    assert social_insurance
    assert all("Pracy" not in division["name"] for division in social_insurance)
    assert {division["type"] for division in social_insurance} == {"Ubezpieczeń Społecznych"}


def test_facts_of_the_recordings_kept_outside_the_repository() -> None:
    """The four whole-text recordings are pinned through their provenance row, not through a body."""
    facts = private_facts()
    split = facts["detail_labour_sentence_split"]
    reasons = facts["detail_labour_reasons"]
    older = facts["detail_sentence_with_reasoning"]
    supreme = facts["detail_supreme"]

    assert set(facts) == {
        "detail_labour_sentence_split",
        "detail_labour_reasons",
        "detail_sentence_with_reasoning",
        "detail_supreme",
    }
    assert (split["judgmentType"], reasons["judgmentType"]) == ("SENTENCE", "REASONS")
    assert split["caseNumber"] == reasons["caseNumber"] == request_params(CASE_NUMBER)["caseNumber"]
    assert split["divisionId"] == reasons["divisionId"]
    assert (
        split["divisionType"] == reasons["divisionType"] == older["divisionType"] == "Pracy i Ubezpieczeń Społecznych"
    )
    assert split["h2"] == "1" and split["uzasadnienieWords"] == "0"
    assert reasons["h2"] == "1" and int(reasons["uzasadnienieWords"]) >= 1
    assert int(reasons["textChars"]) > int(split["textChars"])
    assert older["judgmentType"] == "SENTENCE" and older["h2"] == "2" and int(older["uzasadnienieWords"]) >= 2
    assert int(older["judgmentDate"][:4]) < 2016 <= int(reasons["judgmentDate"][:4])
    for name in ("detail_labour_sentence_split", "detail_labour_reasons", "detail_sentence_with_reasoning"):
        assert facts[name]["cssClasses"] == "anon-block" and int(facts[name]["anonBlocks"]) > 0, name
    assert split["referencedRegulations"] == "0"
    assert int(reasons["referencedRegulations"]) > 0
    for name in ("detail_labour_reasons", "detail_sentence_with_reasoning", "detail_supreme"):
        assert facts[name]["regsWithYearAndEntry"] == facts[name]["referencedRegulations"], name
    assert int(reasons["isapLinks"]) > 0
    assert (split["judgmentUrlScheme"], older["judgmentUrlScheme"]) == ("https", "http")
    assert (
        supreme["courtType"] == "SUPREME"
        and supreme["sourceCode"] == "SUPREME_COURT"
        and supreme["divisionType"] == "none"
    )


def test_no_recording_holds_an_e_mail_address_or_a_phone_number() -> None:
    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf-8")

        assert not EMAIL.search(text), path.name
        assert not PHONE.search(text), path.name


def test_the_numbers_derived_from_the_recordings_are_on_record() -> None:
    """The counts and dates that later changes quote are kept in the provenance next to the hashes."""
    text = provenance_text()
    totals = [
        total(UNBOUNDED),
        total(BOUNDED),
        total(SUPREME),
        total("search_newest_constitutional_tribunal.json"),
        total("search_newest_national_appeal_chamber.json"),
        total(DIVISION_VIII),
        total(SENTENCES_ALL),
        total(REASONS_ALL),
        total(LAW_JOURNAL),
    ]
    newest = [
        load_json(name)["items"][0]["judgmentDate"]
        for name in (
            BOUNDED,
            SUPREME,
            "search_newest_constitutional_tribunal.json",
            "search_newest_national_appeal_chamber.json",
        )
    ]

    for number in totals:
        assert thousands(number) in text, number
    for date in newest:
        assert date in text, date
    for year in YEARS:
        row = f"| {year} | {thousands(total(f'search_viii_sentence_{year}.json'))} | {thousands(total(f'search_viii_reasons_{year}.json'))} |"
        assert row in text, year
    assert load_json(PAGE_SIZE_ERROR)["error"]["message"].split(" : ", 1)[1] in text


def search_items() -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, item) for name in provenance_rows() if name.startswith("search_") for item in load_json(name)["items"]
    ]


def test_judge_names_are_redacted_and_their_roles_are_kept() -> None:
    """No recording keeps a judge's name; the number replaced per file is on record."""
    redacted = redaction_rows()
    roles: set[str] = set()
    for name in provenance_rows():
        judges = (
            [judge for item in load_json(name).get("items", []) for judge in item["judges"]]
            if name.startswith("search_")
            else []
        )

        assert all(judge["name"] == "[redacted]" for judge in judges), name
        assert all(set(judge) == {"name", "function", "specialRoles"} for judge in judges), name
        assert len(judges) == redacted.get(name, (0, 0))[0], name
        roles |= {role for judge in judges for role in judge["specialRoles"]}
    assert "PRESIDING_JUDGE" in roles
    assert set(redacted) <= set(provenance_rows())


def test_excerpts_mask_names_with_x_and_keep_their_length() -> None:
    """Masked words are runs of `x` as long as the word, so an excerpt is as long as it was captured."""
    for name, (_judges, words) in redaction_rows().items():
        masked = [
            item["textContent"]
            for item in load_json(name)["items"]
            if re.search(r"(?<![\w])x{3,}(?![\w])", item["textContent"])
        ]

        assert bool(masked) == (words > 0), name


def test_a_record_reads_the_same_in_every_file_that_holds_it() -> None:
    excerpts: dict[int, str] = {}
    for _name, item in search_items():
        assert excerpts.setdefault(item["id"], item["textContent"]) == item["textContent"], item["id"]


# A person's name follows these role words in a court caption. After redaction only `x` masks, court
# terms, a court town in `SSA w SO w <town>` and the court's own `(...)` may follow them.
PERSON_CUE = re.compile(
    r"(?:\bSS[AORN]\b|\b[Ss]ędzi[ae]\b|\b[Pp]rzewodnicząc\w+|\b[Pp]rotokolant\w*|\bstażysta\b|\bsekretarz\b|\bsekr\.|\b[Pp]rokuratora\b|\badwokata\b)"
    r"(?:[\s:,.;()–-]|SO\b|SA\b|SR\b|SN\b|sąd\.|sądowy\b|starszy\b|st\.|spr\.|w\b|przewodniczący\b|sprawozdawca\b)*"
    r"(?P<next>[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)"
)
ALLOWED_AFTER_CUE = {
    "Sąd",
    "Sądu",
    "Sędziowie",
    "Sędzia",
    "Protokolant",
    "Protokolantka",
    "Przewodniczący",
    "Przewodnicząca",
    "Prokuratora",
    "Generalnego",
    "Gdańsku",
}


def test_no_name_follows_a_role_cue_in_an_excerpt() -> None:
    """Guards the redaction: a capitalised word right after a judge, clerk or prosecutor cue would be a name."""
    for name, item in search_items():
        for match in PERSON_CUE.finditer(item["textContent"]):
            assert match.group("next") in ALLOWED_AFTER_CUE, (name, item["id"], match.group(0))


OUT_OF_RANGE = "search_out_of_range_page.json"


def probe_facts() -> dict[str, tuple[dict[str, str], int]]:
    """Facts of the requests whose body is not stored, keyed by probe name, with the body size."""
    out: dict[str, tuple[dict[str, str], int]] = {}
    for line in provenance_text().splitlines():
        if not line.startswith("| `probe_"):
            continue
        cells = table_cells(line)
        out[cells[0]] = (dict(pair.split("=", 1) for pair in cells[7].split("; ")), int(re.sub(r"\D", "", cells[4])))
    return out


def test_page_numbers_are_zero_based_and_a_hundred_record_page_is_about_140_kb() -> None:
    facts, size = probe_facts()["probe_page_number_base"]
    recorded = load_json(DIVISION_VIII)
    rels = {link["rel"]: link["href"] for link in recorded["links"]}

    assert "pageNumber=0" in rels["self"] and "pageNumber=1" in rels["next"]
    assert facts["overlapWithRecordedPage0"] == "0"
    assert facts["selfLinkPageNumber"] == facts["pageNumber"] == "1"
    assert facts["items"] == facts["pageSize"] == "100"
    assert 100_000 < size < 200_000


def test_a_page_past_the_end_is_empty_keeps_the_total_and_has_no_next_link() -> None:
    page = load_json(OUT_OF_RANGE)
    params = request_params(OUT_OF_RANGE)
    page_size, page_number = int(params["pageSize"]), int(params["pageNumber"])

    assert page["items"] == []
    assert provenance_rows()[OUT_OF_RANGE][2] == "200"
    assert [link["rel"] for link in page["links"]] == ["self", "prev"]
    # The first page number past the end: it starts at or beyond the total, and the one before it does not.
    assert page_number * page_size >= total(OUT_OF_RANGE) > (page_number - 1) * page_size
    assert total(OUT_OF_RANGE) >= total(DIVISION_VIII)


def test_the_division_code_filter_is_an_exact_value_that_selects_the_division() -> None:
    facts, _size = probe_facts()["probe_division_code_filter"]
    recorded_codes = {item["division"]["code"] for item in load_json(DIVISION_VIII)["items"]}

    assert recorded_codes == {facts["ccDivisionCode"]} == {facts["codesReturned"]}
    assert facts["items"] == facts["pageSize"] == "20"
    assert int(facts["totalResults"]) >= total(DIVISION_VIII)
    assert int(facts["distinctCourts"]) >= 2


def test_the_supreme_court_labour_chamber_name_as_written_matches_no_record() -> None:
    """The exact-name chamber filter returned nothing: the stored chamber name differs from this spelling."""
    facts, _size = probe_facts()["probe_supreme_labour_chamber_name"]
    recorded_chambers = {
        chamber["name"] for item in load_json(SUPREME)["items"] for chamber in item["division"]["chambers"]
    }

    assert facts["courtType"] == "SUPREME"
    assert facts["items"] == facts["totalResults"] == "0"
    assert facts["queryTemplateScChamberName"] == "echoed"
    assert facts["scChamberName"] not in recorded_chambers
    assert recorded_chambers == {"Izba Karna"}


CHAMBERS = "supreme_chambers_metadata_2016.json"
LABOUR_CHAMBER = "Izba Pracy, Ubezpieczeń Społecznych i Spraw Publicznych"


def test_the_supreme_court_chamber_page_is_metadata_only_and_the_newest_up_to_the_last_date() -> None:
    page = load_json(CHAMBERS)
    items = page["items"]
    row = provenance_rows()[CHAMBERS]
    params = request_params(CHAMBERS)

    assert set(page) == {"links", "items", "info"}
    assert len(items) == int(params["pageSize"]) == 100
    assert all(set(item) == {"id", "courtType", "division", "judgmentDate"} for item in items)
    assert all(item["courtType"] == "SUPREME" for item in items)
    assert all(set(item["division"]) == {"href", "id", "name", "chambers"} for item in items)
    assert all(set(chamber) == {"href", "id", "name"} for item in items for chamber in item["division"]["chambers"])
    dates = [item["judgmentDate"] for item in items]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] <= params["judgmentDateTo"] == "2016-06-22"
    assert page["info"]["totalResults"] == total(SUPREME)
    assert row[2] == "200" and row[9] == "yes"
    assert len((FIXTURES / CHAMBERS).read_bytes()) < 100_000


def test_the_supreme_court_labour_chamber_is_recorded_under_its_older_long_name() -> None:
    """The chamber names on the page, and which of them concern labour; the rule of `labour_only` follows this."""
    items = load_json(CHAMBERS)["items"]
    names = collections.Counter(chamber["name"] for item in items for chamber in item["division"]["chambers"])
    labour_names = {name for name in names if "prac" in name.casefold() or "ubezpiecz" in name.casefold()}
    probe_5, _size = probe_facts()["probe_supreme_labour_chamber_name"]

    assert set(names) == {"Izba Cywilna", "Izba Karna", "Izba Wojskowa", LABOUR_CHAMBER}
    assert labour_names == {LABOUR_CHAMBER}
    assert names[LABOUR_CHAMBER] == 15
    assert LABOUR_CHAMBER.startswith("Izba Pracy")
    assert probe_5["scChamberName"] not in names
    assert all(len(item["division"]["chambers"]) == 1 for item in items)


DETAIL = "judgment_labour_sentence_metadata.json"
DETAIL_RECORDING = "detail_labour_sentence_split"


def private_rows() -> dict[str, list[str]]:
    """Cells of the table of recordings kept outside the repository, keyed by recording name."""
    return {
        table_cells(line)[0]: table_cells(line)
        for line in provenance_text().splitlines()
        if line.startswith("| `detail_")
    }


def test_the_stored_judgment_detail_is_metadata_only() -> None:
    """The reduction keeps the wrapper, the id, the portal address and the division type, and nothing else."""
    body = load_json(DETAIL)

    assert set(body) == {"links", "data"}
    assert all(set(link) == {"rel", "href"} for link in body["links"])
    assert set(body["data"]) == {"id", "source", "division"}
    assert set(body["data"]["source"]) == {"judgmentUrl"}
    assert set(body["data"]["division"]) == {"type"}
    assert len((FIXTURES / DETAIL).read_bytes()) < 1000


def test_the_stored_judgment_detail_is_the_record_pinned_in_the_private_table() -> None:
    """Same request, same size and hash of the original body, same division type and portal scheme."""
    body = load_json(DETAIL)
    row = provenance_rows()[DETAIL]
    private = private_rows()[DETAIL_RECORDING]
    facts = private_facts()[DETAIL_RECORDING]

    assert row[1] == private[1] == f"https://www.saos.org.pl/api/judgments/{body['data']['id']}"
    assert (row[6], row[7]) == (private[4], private[5])
    assert body["data"]["division"]["type"] == facts["divisionType"] == "Pracy i Ubezpieczeń Społecznych"
    assert body["data"]["source"]["judgmentUrl"].split(":", 1)[0] == facts["judgmentUrlScheme"]
    assert [link["rel"] for link in body["links"]] == ["self"]
    assert body["links"][0]["href"] == row[1]


SKELETON_PRIVATE = {
    "judgment_skeleton_sentence_split.json": "detail_labour_sentence_split",
    "judgment_skeleton_reasons.json": "detail_labour_reasons",
    "judgment_skeleton_sentence_nested.json": "detail_sentence_with_reasoning",
    "judgment_skeleton_supreme.json": "detail_supreme",
}
SKELETON_KEYS = {"id", "courtType", "courtCases", "judgmentType", "judgmentDate", "division", "source", "textContent"}
COURT_FORMULAE = {
    "WYROK",
    "UZASADNIENIE",
    "POSTANOWIENIE",
    "UCHWAŁA",
    "ZARZĄDZENIE",
    "W IMIENIU RZECZYPOSPOLITEJ POLSKIEJ",
}
HEADING = re.compile(r"<(h[1-6])>(.*?)</\1>", re.DOTALL)
TAG = re.compile(r"<[^>]*>")
ALLOWED_TAG = re.compile(r'</?[a-z][a-z0-9]*>|<span class="anon-block">')
ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#\d+|#[xX][0-9A-Fa-f]+);")
ANON_BLOCK = '<span class="anon-block">'
LONE_SENTENCE = "search_case_number_lone_sentence.json"


def skeleton_text(name: str) -> str:
    return str(load_json(name)["data"]["textContent"])


def heading_titles(html: str) -> list[str]:
    return [" ".join(TAG.sub("", match.group(2)).split()) for match in HEADING.finditer(html)]


def skeleton_violations(html: str) -> list[str]:
    """What a skeleton text must not hold: an attribute, a letter other than `x`, a long digit run.

    Court formulae in headings are the only words allowed to stay.
    """

    def drop_formula(match: re.Match[str]) -> str:
        title = " ".join(TAG.sub("", match.group(2)).split())
        return "" if title in COURT_FORMULAE else match.group(0)

    problems = [tag for tag in TAG.findall(html) if not ALLOWED_TAG.fullmatch(tag)]
    text = ENTITY.sub("", TAG.sub("", HEADING.sub(drop_formula, html)))
    problems += re.findall(r"[^\W\d_]", text.replace("x", ""))
    problems += [run for run in re.findall(r"\d{4,}", text) if set(run) != {"0"}]
    return problems


def skeleton_facts() -> dict[str, dict[str, str]]:
    """Facts of the skeleton files from the Skeleton details table, keyed by file name."""
    out: dict[str, dict[str, str]] = {}
    for line in provenance_text().splitlines():
        if not line.startswith("| `judgment_skeleton_"):
            continue
        cells = table_cells(line)
        if len(cells) == 4:
            out[cells[0]] = dict(pair.split("=", 1) for pair in cells[2].split("; "))
    return out


def test_the_skeleton_scan_catches_a_letter_an_attribute_and_a_long_number() -> None:
    """Positive control: the scan used below fails on a dirty sample and passes a clean one."""
    clean = f"<h2>WYROK</h2><p>xxx 0000 12 {ANON_BLOCK}(...)</span> &nbsp;</p>"
    dirty = '<p class="a">Jan Kowalski 12345678901</p><h2>Jan Kowalski</h2>'

    assert skeleton_violations(clean) == []
    found = skeleton_violations(dirty)
    assert '<p class="a">' in found
    assert "J" in found and "K" in found
    assert "12345678901" in found


def test_a_skeleton_holds_the_structure_of_a_judgment_text_and_none_of_its_words() -> None:
    for name in SKELETON_PRIVATE:
        body = load_json(name)
        data = body["data"]

        assert set(body) == {"data"}, name
        assert set(data) == SKELETON_KEYS, name
        assert skeleton_violations(data["textContent"]) == [], name
        assert set(data["source"]) == {"code", "judgmentUrl"}, name
        assert all(set(case) == {"caseNumber"} for case in data["courtCases"]), name
    assert skeleton_text("judgment_skeleton_supreme.json").count("\n") == int(
        skeleton_facts()["judgment_skeleton_supreme.json"]["newlines"]
    )


def test_the_facts_of_a_skeleton_are_the_ones_on_record() -> None:
    facts = skeleton_facts()

    assert set(facts) == set(SKELETON_PRIVATE)
    for name, recording in SKELETON_PRIVATE.items():
        html = skeleton_text(name)
        recorded = facts[name]
        private = private_facts()[recording]
        has_markup = "<" in html

        assert len(html) == int(recorded["skeletonTextChars"]), name
        assert recorded["rawTextChars"] == private["textChars"], name
        assert recorded["textFormat"] == ("html" if has_markup else "plain"), name
        assert html.count("<h2>") == int(recorded["h2"]) == int(private["h2"]), name
        assert html.count(ANON_BLOCK) == int(recorded["anonBlocks"]) == int(private["anonBlocks"]), name
        assert (",".join(heading_titles(html)) or "none") == recorded["headings"], name
        assert load_json(name)["data"]["judgmentType"] == private["judgmentType"], name
        assert load_json(name)["data"]["courtCases"][0]["caseNumber"] == private["caseNumber"], name


def test_a_skeleton_is_the_detail_recorded_before_and_the_service_has_not_changed_it() -> None:
    """Same request, same original size and SHA-256 as the private recording of the same id."""
    for name, recording in SKELETON_PRIVATE.items():
        row = provenance_rows()[name]
        private = private_rows()[recording]

        assert row[1] == private[1], name
        assert (row[6], row[7]) == (private[4], private[5]), name
        assert row[1].endswith(f"/{load_json(name)['data']['id']}"), name


def test_the_sentence_skeleton_has_no_reasoning_heading_and_the_reasons_skeleton_starts_with_one() -> None:
    sentence = load_json("judgment_skeleton_sentence_split.json")["data"]
    reasons = load_json("judgment_skeleton_reasons.json")["data"]
    nested = load_json("judgment_skeleton_sentence_nested.json")["data"]

    assert (sentence["judgmentType"], reasons["judgmentType"], nested["judgmentType"]) == (
        "SENTENCE",
        "REASONS",
        "SENTENCE",
    )
    assert sentence["courtCases"] == reasons["courtCases"]
    assert request_params(CASE_NUMBER)["caseNumber"] == sentence["courtCases"][0]["caseNumber"]
    assert sentence["division"]["court"]["id"] == reasons["division"]["court"]["id"]
    assert sentence["division"]["id"] == reasons["division"]["id"]
    assert "UZASADNIENIE" not in heading_titles(sentence["textContent"])
    assert heading_titles(reasons["textContent"]) == ["UZASADNIENIE"]
    assert heading_titles(nested["textContent"])[-1] == "UZASADNIENIE"
    assert sentence["division"]["code"].endswith("21")


def test_a_case_number_can_find_a_sentence_with_no_reasons_record() -> None:
    page = load_json(LONE_SENTENCE)
    item = page["items"][0]

    assert page["info"]["totalResults"] == len(page["items"]) == 1
    assert item["judgmentType"] == "SENTENCE"
    assert item["courtCases"][0]["caseNumber"] == request_params(LONE_SENTENCE)["caseNumber"]
    assert request_params(LONE_SENTENCE)["pageSize"] == request_params(CASE_NUMBER)["pageSize"] == "10"
    assert set(request_params(LONE_SENTENCE)) == set(request_params(CASE_NUMBER))
    assert item["id"] == load_json("search_viii_sentence_2016.json")["items"][0]["id"]
