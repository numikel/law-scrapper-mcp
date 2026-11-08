"""`search_saos_judgments`: search Polish court judgments in SAOS."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse
from law_scrapper_mcp.tools.annotations import READ_ONLY_REMOTE
from law_scrapper_mcp.tools.error_handling import handle_tool_errors
from law_scrapper_mcp_saos.models import SearchSaosJudgmentsOutput
from law_scrapper_mcp_saos.service import search_saos_judgments as run_search


def register(mcp: MCPServer[AppContext]) -> None:
    """Register the judgment search tool."""

    @mcp.tool(
        title="Wyszukiwanie orzeczeń w SAOS",
        annotations=READ_ONLY_REMOTE,
        meta={"tags": ["search", "judgments", "saos"]},
    )
    @handle_tool_errors
    async def search_saos_judgments(
        ctx: Context[AppContext],
        phrase: Annotated[
            str | None,
            Field(
                description=(
                    "Fraza szukana w treści orzeczeń (do 500 znaków), np. 'wypadek przy pracy'. "
                    "Uwaga: fraza trafia do serwisu saos.org.pl."
                ),
            ),
        ] = None,
        signature: Annotated[
            str | None,
            Field(
                description=(
                    "Sygnatura sprawy (do 100 znaków), np. 'VIII U 2924/25'. Zwraca wyrok i jego uzasadnienie "
                    "(dwa osobne rekordy). Uwaga: sygnatura trafia do serwisu saos.org.pl."
                ),
            ),
        ] = None,
        date_from: Annotated[
            str | None,
            Field(description="Najwcześniejsza data orzeczenia (RRRR-MM-DD). Nie może być późniejsza niż dziś."),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(
                description=(
                    "Najpóźniejsza data orzeczenia (RRRR-MM-DD). Przyszłe daty są obcinane do dziś, bo SAOS "
                    "zawiera rekordy z błędnymi datami z przyszłości. Domyślnie: dziś."
                ),
            ),
        ] = None,
        court_type: Annotated[
            str | None,
            Field(
                description=(
                    "Rodzaj sądu: 'COMMON' (sądy powszechne), 'SUPREME' (Sąd Najwyższy), "
                    "'CONSTITUTIONAL_TRIBUNAL' (Trybunał Konstytucyjny), 'NATIONAL_APPEAL_CHAMBER' "
                    "(Krajowa Izba Odwoławcza), 'ADMINISTRATIVE' (sądy administracyjne; w SAOS brak rekordów). "
                    "Domyślnie: wszystkie rodzaje."
                ),
            ),
        ] = None,
        judgment_type: Annotated[
            str | None,
            Field(
                description=(
                    "Rodzaj orzeczenia: 'Wyrok', 'Postanowienie', 'Uchwała', 'Zarządzenie' albo 'Uzasadnienie' "
                    "(także nazwy SAOS: SENTENCE, DECISION, RESOLUTION, REGULATION, REASONS). "
                    "Domyślnie: wszystkie rodzaje."
                ),
            ),
        ] = None,
        limit: Annotated[
            str | int | None,
            Field(description="Liczba orzeczeń na stronie (domyślnie 20, maks. 100; większa wartość jest przycinana)."),
        ] = 20,
        offset: Annotated[
            str | int | None,
            Field(
                description=(
                    "Nieujemne przesunięcie: pozycja pierwszego zwracanego orzeczenia na liście SAOS "
                    "(najnowsze pierwsze). Dowolna wartość; gdy nie jest wielokrotnością limit, serwer pobiera "
                    "dwie strony SAOS. Przy labour_only: pozycja na liście SAOS, od której zaczyna się skan "
                    "(nie numer trafienia); kolejną wartość podaje scan.next_offset."
                ),
            ),
        ] = 0,
        include_portal_links: Annotated[
            bool | str,
            Field(
                description=(
                    "Gdy true, pobiera odnośnik do portalu sądu dla pierwszych 5 wyników strony: do 5 dodatkowych "
                    "żądań do SAOS (4, gdy offset nie jest wielokrotnością limit), wykonywanych kolejno. Odnośniki "
                    "pozostałych wyników pobierze get_saos_judgment_details. Domyślnie false."
                ),
            ),
        ] = False,
        labour_only: Annotated[
            bool | str,
            Field(
                description=(
                    "Gdy true, zwraca tylko orzeczenia z wydziałów pracy sądów powszechnych (kod wydziału kończy się "
                    "na 15, 18 lub 21) albo z Izby Pracy Sądu Najwyższego (court_type='SUPREME'; nazwa izby zaczyna "
                    "się od 'Izba Pracy'), skanując kolejne strony SAOS po 100 rekordów: do 5 żądań do SAOS na "
                    "wywołanie, zatrzymanie po limit trafieniach. Bez total_count i page_info; kursor jest w polu scan "
                    "(scan.next_offset podaj jako offset następnego wywołania). court_type: brak, 'COMMON' lub "
                    "'SUPREME'; pozostałe rodzaje sądów są odrzucane. Nie łączy się z include_portal_links. "
                    "Domyślnie false."
                ),
            ),
        ] = False,
    ) -> EnrichedResponse[SearchSaosJudgmentsOutput]:
        """
        Wyszukaj orzeczenia sądów i trybunałów w SAOS (System Analizy Orzeczeń Sądowych, www.saos.org.pl).

        Zwraca strony orzeczeń od najnowszych: sygnaturę, sąd, wydział, rodzaj, datę, fragment tekstu (do 800
        znaków) i odnośnik do orzeczenia w SAOS. Wynik trafia do zestawu (result_set_id), który zawęzisz
        narzędziem filter_results po sygnaturze, sądzie, wydziale, kodzie wydziału, rodzaju i dacie.
        Odnośnik do portalu sądu pobierzesz osobno (get_saos_judgment_details) albo dla pierwszych 5 wyników
        od razu (include_portal_links=true).

        Uwagi o danych: wyrok (SENTENCE) i jego uzasadnienie (REASONS) to dwa osobne rekordy tej samej sprawy,
        każdy z własną datą. Zasięg SAOS jest różny dla każdego rodzaju sądu (część kończy się na latach
        2015-2018, sądów administracyjnych nie ma wcale): aktualne liczby i daty najnowszych orzeczeń podaje
        get_system_metadata(category="coverage"). Na pierwszej stronie wyniku pojawia się ostrzeżenie tylko
        wtedy, gdy zapytanie z court_type sięga dalej niż najnowsze orzeczenie tego rodzaju sądu.
        Brak wyniku nie dowodzi, że orzeczenie nie istnieje. Fraza i sygnatura są wysyłane do saos.org.pl.

        Wydziały pracy sądów powszechnych: po wyszukaniu użyj filter_results z pattern="(15|18|21)$"
        i field="division_code" (wzorzec "(15|21)$" pomija wydziały wyłącznie ubezpieczeń społecznych;
        po nazwie wydziału działa field="division", pattern="Pracy"). Gdy strona zawiera mało orzeczeń pracy,
        użyj labour_only=true: serwer skanuje do 5 stron SAOS po 100 rekordów (do 5 żądań) i zwraca najwyżej
        limit orzeczeń z wydziałów pracy (kod wydziału kończy się na 15, 18 lub 21; w Sądzie Najwyższym (court_type="SUPREME")
        nazwa izby zaczyna się od "Izba Pracy", a po wyszukaniu zawęża field="division", pattern="Izba Pracy").
        To skan, a nie wyszukiwanie
        po wydziale: nie zna liczby wszystkich trafień (total_count i page_info są puste), a pole scan podaje
        przeskanowany zakres, regułę i scan.next_offset. W tym trybie offset to pozycja na liście SAOS, więc
        kolejne wywołanie przekaż z offset=scan.next_offset. Zestaw ze skanu obejmuje tylko przeskanowane okno,
        więc brak dopasowania w filter_results nie dowodzi, że orzeczenia nie ma.

        Kiedy użyć: Szukasz orzeczeń sądów lub trybunałów po frazie, sygnaturze, dacie, rodzaju sądu lub rodzaju
        orzeczenia.
        Kiedy użyć labour_only: Chcesz orzeczenia wydziałów pracy i ubezpieczeń społecznych sądów powszechnych
        albo Izby Pracy Sądu Najwyższego, a fraza lub daty nie zawężają wyników do nich.
        Kiedy NIE używać labour_only: Szukasz orzeczeń Trybunału lub KIO (odrzucane), chcesz
        odnośników do portalu sądu w tym samym wywołaniu (nie łączy się z include_portal_links) albo potrzebujesz
        liczby wszystkich orzeczeń pracy (skan jej nie zna).
        Kiedy NIE używać: Szukasz aktów prawnych → użyj search_legal_acts.
        Kiedy NIE używać: Chcesz zawęzić już pobrany zestaw orzeczeń → użyj filter_results z result_set_id.
        Kiedy NIE używać: Potrzebujesz pełnego tekstu uzasadnienia — to narzędzie zwraca tylko fragment.
        Kiedy NIE używać: Potrzebujesz odnośnika do portalu sądu dla jednego orzeczenia → użyj
        get_saos_judgment_details.

        Przykłady:
        - search_saos_judgments(phrase="wypadek przy pracy") - Najnowsze orzeczenia o wypadku przy pracy
        - search_saos_judgments(signature="VIII U 2924/25") - Wyrok i uzasadnienie jednej sprawy
        - search_saos_judgments(court_type="SUPREME", judgment_type="Uchwała") - Uchwały Sądu Najwyższego
        - search_saos_judgments(phrase="mobbing", date_from="2020-01-01", date_to="2022-12-31") - Zakres dat
        - search_saos_judgments(phrase="zadośćuczynienie", limit=50, offset=50) - Druga strona po 50 wyników
        - search_saos_judgments(court_type="CONSTITUTIONAL_TRIBUNAL", limit=10) - Dziesięć orzeczeń Trybunału
        - search_saos_judgments(signature="VIII U 2924/25", include_portal_links=True) - Z odnośnikami do portalu sądu
        - search_saos_judgments(phrase="wypadek przy pracy", labour_only=True) - Tylko wydziały pracy, skan do 5 stron
        - search_saos_judgments(phrase="mobbing", labour_only=True, limit=10, offset=300) - Skan od pozycji 300 listy SAOS
        - search_saos_judgments(court_type="SUPREME", labour_only=True, limit=10) - Izba Pracy Sądu Najwyższego
        """
        return await run_search(
            get_app_context(ctx),
            phrase=phrase,
            signature=signature,
            date_from=date_from,
            date_to=date_to,
            court_type=court_type,
            judgment_type=judgment_type,
            limit=limit,
            offset=offset,
            include_portal_links=include_portal_links,
            labour_only=labour_only,
        )
