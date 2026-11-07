"""The publisher-code to host map of the voivodeship journals.

The journals run one engine (e-Dziennik ELI) on sixteen separate hosts. Each host answers only for
its own acts, and it ignores the publisher code it is asked for, so the code has to be mapped to
the right host here. A journal can be listed and still withheld: it then has a code but no client,
and a request for it fails with a named reason instead of reaching any host.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from law_scrapper_mcp.services.act_sources import PublisherInfo, fold_publisher_name

CODE_PREFIX = "POL_WOJ_"

_TLS_CHAIN = "niepełny łańcuch certyfikatów TLS, weryfikacja nie przechodzi"
_NO_ANSWER = "serwer nie odpowiada na żądania (0 z 8 prób w dniach 2026-11-01 i 2026-11-02)"


@dataclass(frozen=True, slots=True)
class Journal:
    """One voivodeship journal.

    Attributes:
        code: Publisher code, `POL_WOJ_` plus two letters.
        voivodeship: Voivodeship the journal belongs to. Shown to agents, and accepted in place of
            the code in the `publisher` parameter (compared case-folded and without diacritics).
        host: Host name of the journal's API.
        short_name: The journal's own short name.
        withheld: Polish reason the journal is not served, or `None` when it is.
    """

    code: str
    voivodeship: str
    host: str
    short_name: str
    withheld: str | None = None

    @property
    def base_url(self) -> str:
        """Address every request path of this journal is appended to."""
        return f"https://{self.host}/api/eli"

    def pdf_url(self, publisher: str, year: int, pos: int) -> str:
        """Public address of an act's PDF on this host."""
        return f"{self.base_url}/acts/{publisher}/{year}/{pos}/text.pdf"

    def publisher_info(self) -> PublisherInfo:
        """How the journal appears in `get_system_metadata` and in the refusal of an unknown publisher."""
        item: dict[str, object] = {
            "code": self.code,
            "voivodeship": self.voivodeship,
            "shortName": self.short_name,
            "available": self.withheld is None,
        }
        if self.withheld is not None:
            item["withheldReason"] = self.withheld
        return PublisherInfo(code=self.code, label=self.voivodeship, available=self.withheld is None, item=item)


JOURNALS: tuple[Journal, ...] = (
    Journal("POL_WOJ_DS", "dolnośląskie", "edzienniki.duw.pl", "Dz. Urz. Woj. Dolnośląskiego"),
    Journal("POL_WOJ_KP", "kujawsko-pomorskie", "edzienniki.bydgoszcz.uw.gov.pl", "Dz. Urz. Woj. Kujawsko-Pomorskiego"),
    Journal("POL_WOJ_LB", "lubelskie", "edziennik.lublin.uw.gov.pl", "Dz. Urz. Woj. Lubelskiego"),
    Journal("POL_WOJ_LS", "lubuskie", "dzienniki.luw.pl", "Dz. Urz. Woj. Lubuskiego", _TLS_CHAIN),
    Journal("POL_WOJ_LD", "łódzkie", "dziennik.lodzkie.eu", "Dz. Urz. Woj. Łódzkiego", _TLS_CHAIN),
    Journal("POL_WOJ_MP", "małopolskie", "edziennik.malopolska.uw.gov.pl", "Dz. Urz. Woj. Małopolskiego", _TLS_CHAIN),
    Journal("POL_WOJ_MZ", "mazowieckie", "edziennik.mazowieckie.pl", "Dz. Urz. Woj. Mazowieckiego", _NO_ANSWER),
    Journal("POL_WOJ_OP", "opolskie", "duwo.opole.uw.gov.pl", "Dz. Urz. Woj. Opolskiego"),
    Journal("POL_WOJ_PK", "podkarpackie", "edziennik.rzeszow.uw.gov.pl", "Dz. Urz. Woj. Podkarpackiego"),
    Journal("POL_WOJ_PL", "podlaskie", "edziennik.bialystok.uw.gov.pl", "Dz. Urz. Woj. Podlaskiego"),
    Journal("POL_WOJ_PM", "pomorskie", "edziennik.gdansk.uw.gov.pl", "Dz. Urz. Woj. Pomorskiego"),
    Journal("POL_WOJ_SL", "śląskie", "dzienniki.slask.eu", "Dz. Urz. Woj. Śląskiego"),
    Journal("POL_WOJ_SK", "świętokrzyskie", "edziennik.kielce.uw.gov.pl", "Dz. Urz. Woj. Świętokrzyskiego"),
    Journal("POL_WOJ_WM", "warmińsko-mazurskie", "edzienniki.olsztyn.uw.gov.pl", "Dz. Urz. Woj. Warmińsko-Mazurskiego"),
    Journal("POL_WOJ_WP", "wielkopolskie", "edziennik.poznan.uw.gov.pl", "Dz. Urz. Woj. Wielkopolskiego"),
    Journal("POL_WOJ_ZP", "zachodniopomorskie", "e-dziennik.szczecin.uw.gov.pl", "Dz. Urz. Woj. Zachodniopomorskiego"),
)

SERVED: tuple[Journal, ...] = tuple(journal for journal in JOURNALS if journal.withheld is None)
WITHHELD: tuple[Journal, ...] = tuple(journal for journal in JOURNALS if journal.withheld is not None)
BY_CODE: dict[str, Journal] = {journal.code: journal for journal in JOURNALS}
PUBLISHER_INFO: tuple[PublisherInfo, ...] = tuple(journal.publisher_info() for journal in JOURNALS)


def names_index(journals: Iterable[Journal]) -> dict[str, Journal]:
    """The journals by folded voivodeship name: how a name typed by the agent finds its journal."""
    return {fold_publisher_name(journal.voivodeship): journal for journal in journals}


def _check_invariants() -> None:
    """Fail at import, not at the first request, when the table is inconsistent."""
    codes = [journal.code for journal in JOURNALS]
    hosts = [journal.host for journal in JOURNALS]
    if len(set(codes)) != len(codes) or len(set(hosts)) != len(hosts):
        raise ValueError("Katalog dzienników: kody i hosty muszą być unikalne.")
    bad = [code for code in codes if not code.startswith(CODE_PREFIX)]
    if bad:
        raise ValueError(f"Katalog dzienników: kody bez prefiksu {CODE_PREFIX}: {', '.join(bad)}")
    names = [fold_publisher_name(journal.voivodeship) for journal in JOURNALS]
    if not all(names) or len(set(names)) != len(names):
        raise ValueError("Katalog dzienników: nazwy województw muszą być niepuste i unikalne (po zwinięciu liter).")


_check_invariants()
