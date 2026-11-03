"""Every wire quirk of the journals, pinned to the recordings (no I/O)."""

from __future__ import annotations

from typing import Any

import pytest
from journal_support import load_json

from law_scrapper_mcp_voivodeship_journals import normalise


def pascalise(value: Any) -> Any:
    """The same payload as a PascalCase host would send it."""
    if isinstance(value, dict):
        return {key[:1].upper() + key[1:]: pascalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [pascalise(item) for item in value]
    return value


def test_camelise_gives_both_casings_the_same_keys() -> None:
    camel = load_json("act_detail_camel.json")

    assert normalise.camelise(pascalise(camel)) == camel


def test_camelise_recurses_through_lists_and_leaves_values_alone() -> None:
    assert normalise.camelise({"Items": [{"Eli": "X", "Nested": {"Key": 1}}]}) == {
        "items": [{"eli": "X", "nested": {"key": 1}}]
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-12-31T14:22:28.067", "2025-12-31"),
        ("2025-12-18T00:00:00", "2025-12-18"),
        ("2025-12-18", "2025-12-18"),
        ("0001-01-01T00:00:00", None),
        ("", None),
        (None, None),
        (20251218, None),
        ("not a date", None),
    ],
)
def test_to_date_keeps_the_day_and_drops_sentinels(value: object, expected: str | None) -> None:
    assert normalise.to_date(value) == expected


def test_a_camel_and_a_pascal_search_item_give_equal_models() -> None:
    item = load_json("search_page_camel.json")["items"][0]

    assert normalise.summary(normalise.camelise(pascalise(item))) == normalise.summary(item)


def test_search_publication_date_comes_from_announcement_date() -> None:
    """In `/search` the host swaps the two date fields: `announcementDate` is the publication timestamp."""
    items = load_json("search_page_camel.json")["items"]

    assert any(item["promulgation"][:10] != item["announcementDate"][:10] for item in items), (
        "the recording must contain an act whose two dates differ, or this test proves nothing"
    )
    for item in items:
        assert normalise.summary(item).promulgation_date == item["announcementDate"][:10]


def test_a_search_summary_has_the_canonical_eli_and_no_legal_status() -> None:
    item = load_json("search_page_camel.json")["items"][0]

    result = normalise.summary(item)

    assert result.eli == f"{item['publisher']}/{item['year']}/{item['pos']}"
    assert result.publisher == item["publisher"]
    assert (result.effective_date, result.in_force) == (None, None)
    assert result.type == item["type"]


def test_the_same_act_has_one_publication_date_in_search_and_in_details() -> None:
    """Search and details name the two date fields oppositely; the mapped meaning must agree."""
    detail = load_json("act_detail_camel.json")
    listed = next(
        item
        for item in load_json("search_page_camel.json")["items"]
        if (item["year"], item["pos"]) == (detail["year"], detail["pos"])
    )

    from_search = normalise.summary(listed).promulgation_date
    from_details = normalise.details(detail, detail["publisher"], detail["year"], detail["pos"]).promulgation_date

    assert from_search == from_details == "2025-12-31"


def test_camel_details_map_dates_sentinels_and_flags() -> None:
    raw = load_json("act_detail_camel.json")

    result = normalise.details(raw, "POL_WOJ_PM", 2025, 5051)

    assert result.eli == "POL_WOJ_PM/2025/5051"
    assert "eli" not in raw, "the host sends none; the canonical one is built from the request"
    assert result.announcement_date == "2025-12-18", "the act date, as for the Sejm"
    assert result.promulgation_date == "2025-12-31"
    assert (result.entry_into_force, result.valid_from, result.repeal_date, result.change_date) == (None,) * 4
    assert result.has_pdf is True and result.has_html is True
    assert result.keywords == ["budżet"]
    assert result.references is None
    assert result.type == "Uchwała"
    assert result.toc == [] and result.is_loaded is False


def test_pascal_details_with_a_null_eli_give_the_same_shape() -> None:
    raw = normalise.camelise(load_json("act_detail_pascal.json"))

    result = normalise.details(raw, "POL_WOJ_KP", 2025, 6588)

    assert result.eli == "POL_WOJ_KP/2025/6588"
    assert result.keywords == ["porozumienia", "transport lokalny"]
    assert result.has_pdf is True
    assert result.announcement_date == "2025-12-12"
    assert result.promulgation_date == "2025-12-31"


def test_relations_keep_the_journals_own_keys_and_normalise_the_nested_act() -> None:
    raw = load_json("references_relation.json")

    result = normalise.relations(raw)

    assert list(result) == list(raw), "relation descriptions are the keys, verbatim"
    (entry,) = next(iter(result.values()))
    assert entry["act"]["eli"] == "POL_WOJ_PM/2026/405"
    assert entry["act"]["promulgation"] is None, "the sentinel date is dropped"
    assert entry["act"]["announcementDate"] == "2026-01-22"
    assert entry["date"] == "2026-01-22"


def test_pascal_relations_are_camelised_inside_the_entries_only() -> None:
    raw = {"Opis Relacji": [{"Art": None, "Date": "2026-01-22T00:00:00", "Act": {"Eli": "POL_WOJ_KP/1/2"}}]}

    result = normalise.relations(raw)

    assert list(result) == ["Opis Relacji"]
    assert result["Opis Relacji"][0]["act"]["eli"] == "POL_WOJ_KP/1/2"


def test_a_scalar_relation_value_is_wrapped_in_a_list() -> None:
    result = normalise.relations({"Opis": {"date": "0001-01-01T00:00:00"}})

    assert result == {"Opis": [{"date": None}]}
