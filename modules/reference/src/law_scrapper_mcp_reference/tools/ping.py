"""`reference_ping`: reports that the reference module is installed, enabled and started."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, Hint
from law_scrapper_mcp.tools.annotations import READ_ONLY_LOCAL
from law_scrapper_mcp.tools.error_handling import handle_tool_errors
from law_scrapper_mcp_reference.state import reference_state


class PingOutput(BaseModel):
    """Output of `reference_ping`."""

    module: str = Field(description="Nazwa pakietu modułu")
    version: str = Field(description="Wersja pakietu modułu")
    started: bool = Field(description="Czy hook startowy modułu został wykonany")
    echo: str | None = Field(default=None, description="Przesłany tekst, bez zmian")


def ping_hints() -> list[Hint]:
    """Hints for the one tool this module has."""
    return [
        Hint(message="To moduł demonstracyjny; do pracy z aktami użyj narzędzi wyszukiwania.", tool="search_legal_acts")
    ]


def register(mcp: MCPServer[AppContext]) -> None:
    """Register the ping tool."""

    @mcp.tool(
        title="Sprawdzenie modułu referencyjnego",
        annotations=READ_ONLY_LOCAL,
        meta={"tags": ["utility", "reference"]},
    )
    @handle_tool_errors
    async def reference_ping(
        ctx: Context[AppContext],
        echo: Annotated[
            str | None,
            Field(
                max_length=64,
                description="Opcjonalny krótki tekst (do 64 znaków), który zostanie zwrócony bez zmian.",
            ),
        ] = None,
    ) -> EnrichedResponse[PingOutput]:
        """
        Sprawdź, czy moduł referencyjny jest zainstalowany, włączony i uruchomiony.

        Zwraca nazwę i wersję pakietu modułu oraz potwierdzenie, że jego hook startowy się wykonał.
        Nie wywołuje żadnego zewnętrznego źródła.

        Kiedy użyć:
        - Chcesz sprawdzić, czy moduł referencyjny jest aktywny w tej instalacji serwera.
        - Testujesz mechanizm modułów źródeł (instalacja, wyłączanie zmienną LAW_MCP_SOURCE_MODULES).

        Kiedy NIE używać:
        - Szukasz aktów prawnych: użyj search_legal_acts lub browse_acts.
        - Potrzebujesz danych z zewnętrznego źródła: ten moduł nie ma żadnego.

        Przykłady:
        - reference_ping() - Podstawowe sprawdzenie modułu
        - reference_ping(echo="test") - Sprawdzenie z odesłaniem tekstu
        - reference_ping(echo="") - Pusty tekst jest poprawny i wraca bez zmian
        - reference_ping(echo="moduł działa") - Tekst z polskimi znakami
        - reference_ping(echo=None) - To samo co wywołanie bez parametrów
        """
        state = reference_state(get_app_context(ctx))
        output = PingOutput(module=state.module, version=state.version, started=state.started, echo=echo)
        return EnrichedResponse[PingOutput](data=output, hints=ping_hints())
