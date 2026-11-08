"""Facts the coverage providers rest on, pinned to the 2026-11-08 recordings.

The DU/MP provider reads the publisher list and the first item of the newest year page; the journal
provider must not, because two journals do not list their newest act first. These tests derive each
expectation from the recording, so a refreshed recording that changes a fact turns them red. Where the
recordings came from: `tests/fixtures/sejm/PROVENANCE.md` and the fourth session in
`tests/fixtures/edziennik/PROVENANCE.md`. No test here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEJM = FIXTURES / "sejm"
JOURNALS = FIXTURES / "edziennik"
SERVED = ("ds", "kp", "lb", "op", "pk", "pl", "pm", "sl", "sk", "wm", "wp", "zp")
UNORDERED = ("sk", "wp")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def page(body: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """`totalCount` and items of a search page in either JSON casing."""
    total = body.get("totalCount", body.get("TotalCount"))
    items = body.get("items", body.get("Items"))
    assert isinstance(total, int) and isinstance(items, list)
    return total, items


def field(item: dict[str, Any], name: str) -> Any:
    return item.get(name, item.get(name[0].upper() + name[1:]))


def test_the_sejm_publisher_list_carries_years_and_counts_for_du_and_mp() -> None:
    publishers = {entry["code"]: entry for entry in load(SEJM / "publishers_live.json")}

    assert set(publishers) == {"DU", "MP"}
    for entry in publishers.values():
        assert set(entry) >= {"code", "actsCount", "years"}
        assert isinstance(entry["actsCount"], int) and entry["actsCount"] > 0
        assert max(entry["years"]) == 2026


def test_the_hand_written_publisher_fixture_has_the_live_keys() -> None:
    live = {key for entry in load(SEJM / "publishers_live.json") for key in entry}
    written = {key for entry in load(FIXTURES / "publishers.json") for key in entry}

    assert written <= live


@pytest.mark.parametrize("code", ["du", "mp"])
def test_on_the_sejm_the_newest_year_page_starts_with_the_highest_position(code: str) -> None:
    total, items = page(load(SEJM / f"search_2026_limit1_{code}.json"))

    assert len(items) == 1
    assert items[0]["pos"] == total
    assert items[0]["promulgation"] >= items[0]["announcementDate"], "promulgation is the publication date"


@pytest.mark.parametrize("code", [c for c in SERVED if c not in UNORDERED])
def test_most_journals_list_their_newest_act_first(code: str) -> None:
    total, items = page(load(JOURNALS / f"year_page_2026_{code}.json"))

    assert abs(field(items[0], "pos") - total) <= 1
    assert field(items[0], "announcementDate") >= "2026-11"


@pytest.mark.parametrize("code", UNORDERED)
def test_two_journals_do_not_list_their_newest_act_first(code: str) -> None:
    total, items = page(load(JOURNALS / f"year_page_2026_{code}.json"))
    detail = load(JOURNALS / f"act_detail_last_position_{code}.json")

    assert field(items[0], "pos") < total - 1000, "the first item is far behind the year's count"
    assert field(items[0], "announcementDate") < detail["promulgation"], "a later act exists"


@pytest.mark.parametrize("code", UNORDERED)
def test_the_act_at_the_years_count_is_recent_where_the_order_fails(code: str) -> None:
    total, _ = page(load(JOURNALS / f"year_page_2026_{code}.json"))
    detail = load(JOURNALS / f"act_detail_last_position_{code}.json")

    assert detail["pos"] == total
    assert detail["promulgation"] >= "2026-11", "in the act detail promulgation is the publication timestamp"


@pytest.mark.parametrize("code", UNORDERED)
def test_a_lone_pub_date_from_without_year_is_ignored(code: str) -> None:
    year_total, _ = page(load(JOURNALS / f"year_page_2026_{code}.json"))
    total, items = page(load(JOURNALS / f"pubdatefrom_only_{code}.json"))

    assert total > 10 * year_total, "the whole journal came back, not a 30-day window"
    assert field(items[0], "announcementDate") < "2026-10-09", "the first item predates the bound"
