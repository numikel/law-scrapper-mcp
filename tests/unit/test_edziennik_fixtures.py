"""Facts about the e-Dziennik ELI journals, pinned to the recordings in `fixtures/edziennik/`.

The journals run an engine unlike `api.sejm.gov.pl`: two JSON casings, inverted date
fields, sentinel dates, a `text.html` that is only the first PDF page, and a host that
ignores an unknown publisher code. Each test states one such fact and derives its
expectation from the recording, so a refreshed recording that changes the fact turns the
test red instead of silently invalidating an assumption a later change builds on.

Where the recordings came from, and what was measured without being stored, is in
`tests/fixtures/edziennik/PROVENANCE.md`. No test here touches the network.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pdfplumber

FIXTURES = Path(__file__).parents[1] / "fixtures" / "edziennik"
PROVENANCE = FIXTURES / "PROVENANCE.md"
SENTINEL = "0001-01-01T00:00:00"
NBSP = " "


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def provenance_text() -> str:
    return PROVENANCE.read_text(encoding="utf-8")


def provenance_rows() -> dict[str, list[str]]:
    """Capture-table rows keyed by fixture file name; cells are stripped, backticks removed.

    Only rows whose first cell is a fixture file name count, so the measurement tables further
    down in the same document are not mistaken for fixtures.
    """
    rows: dict[str, list[str]] = {}
    for line in provenance_text().splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if re.fullmatch(r"[a-z0-9_]+\.(json|html|pdf)", cells[0]):
            rows[cells[0]] = cells
    return rows


def day(value: str) -> str:
    """The calendar-day prefix of an ISO timestamp."""
    return value[:10]


def test_every_fixture_has_a_provenance_row_and_every_row_has_a_fixture() -> None:
    on_disk = {p.name for p in FIXTURES.iterdir() if p.name != "PROVENANCE.md"}

    assert on_disk == set(provenance_rows())


def test_binary_fixtures_are_stored_byte_for_byte() -> None:
    """A text-normalising checkout (autocrlf) would change a PDF; the recorded hash catches it."""
    for name in ("scan_no_text.pdf", "text_layer.pdf", "text_page1.html"):
        _, _url, _status, _ctype, size, digest, *_ = provenance_rows()[name]
        data = (FIXTURES / name).read_bytes()

        assert len(data) == int(re.sub(r"\D", "", size)), name
        assert hashlib.sha256(data).hexdigest() == digest, name


def test_json_fixtures_have_the_recorded_structure() -> None:
    """The JSON files are re-indented recordings; their size is checked structurally.

    The upstream serialiser escapes non-ASCII as `\\uXXXX`, so a re-indented file cannot be
    compared to the original bytes. The record counts come from the request in the
    provenance table, not from a constant here.
    """
    rows = provenance_rows()
    for name, key in (("search_page_camel.json", "items"), ("search_page_pascal.json", "Items")):
        limit = int(re.search(r"limit=(\d+)", rows[name][1]).group(1))  # type: ignore[union-attr]

        assert len(load_json(name)[key]) == limit, name
    assert len(load_json("publishers_camel.json")) == 1
    assert len(load_json("publishers_pascal.json")) == 1


def test_publisher_lists_differ_in_key_casing_between_journals() -> None:
    camel = load_json("publishers_camel.json")[0]
    pascal = load_json("publishers_pascal.json")[0]

    assert set(camel) == {"code", "shortName", "name", "years", "deedsCount"}
    assert set(pascal) == {"Code", "ShortName", "Name", "Years", "count"}
    assert NBSP in pascal["ShortName"]


def test_camel_search_uses_lowercase_eli_and_inverted_date_fields() -> None:
    """`promulgation` is the act date and `announcementDate` the publication, the reverse of Sejm."""
    items = load_json("search_page_camel.json")["items"]

    assert all("eli" in item and "ELI" not in item for item in items)
    assert all(day(item["promulgation"]) <= day(item["announcementDate"]) for item in items)
    assert any(day(item["promulgation"]) < day(item["announcementDate"]) for item in items)


def test_pascal_search_wraps_pascal_records_in_a_pascal_envelope() -> None:
    page = load_json("search_page_pascal.json")

    assert {"Items", "Offset", "Count", "TotalCount"} <= set(page)
    assert page["Count"] == len(page["Items"])
    assert all("Eli" in item for item in page["Items"])


def test_camel_detail_has_no_eli_and_six_sentinel_dates() -> None:
    detail = load_json("act_detail_camel.json")
    sentinels = {key for key, value in detail.items() if value == SENTINEL}

    assert "eli" not in detail and "ELI" not in detail
    assert sentinels == {"changeDate", "entryIntoForce", "validFrom", "repealDate", "expirationDate", "legalStatusDate"}
    assert detail["textHTML"] is True and detail["textPDF"] is True


def test_pascal_detail_carries_null_eli_and_null_references() -> None:
    detail = load_json("act_detail_pascal.json")

    assert "Eli" in detail and detail["Eli"] is None
    assert "References" in detail and detail["References"] is None


def test_references_are_keyed_by_one_relation_description_with_woj_eli() -> None:
    relations = load_json("references_relation.json")

    assert len(relations) == 1
    for entries in relations.values():
        assert all(entry["act"]["eli"].startswith("POL_WOJ_") for entry in entries)


def test_a_host_ignores_the_publisher_code_it_is_asked_for() -> None:
    requested = re.search(r"/acts/(POL_WOJ_[A-Z]+)/", provenance_rows()["unknown_publisher.json"][1])
    assert requested is not None
    items = load_json("unknown_publisher.json")["items"]

    assert items
    assert all(item["publisher"] != requested.group(1) for item in items)


def test_a_scanned_pdf_has_images_and_no_text_while_the_other_has_a_text_layer() -> None:
    """Real pdfplumber, no mock: the distinction FR-007 needs between a scan and a text PDF."""
    with pdfplumber.open(FIXTURES / "scan_no_text.pdf") as scan:
        assert scan.pages
        assert all((page.extract_text() or "").strip() == "" for page in scan.pages)
        assert all(len(page.images) >= 1 for page in scan.pages)
    with pdfplumber.open(FIXTURES / "text_layer.pdf") as layered:
        assert "".join((page.extract_text() or "") for page in layered.pages).strip()


def test_the_measurements_taken_without_storing_a_body_are_on_record() -> None:
    """The 204, the unbounded page and the HTML-is-page-one claim rest on numbers kept here."""
    text = provenance_text()

    assert "HTTP 204" in text
    assert "3 136 041 B" in text
    assert "222 755" in text
    assert "2 546" in text
    assert "1 419" in text
