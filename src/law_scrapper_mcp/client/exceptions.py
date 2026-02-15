"""Exception hierarchy for Law Scrapper MCP client."""

from __future__ import annotations


class LawScrapperError(Exception):
    """Base exception for Law Scrapper MCP errors."""

    pass


class SejmApiError(LawScrapperError):
    """Error from Sejm API."""

    def __init__(
        self, message: str, status_code: int | None = None, url: str | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class ActNotFoundError(SejmApiError):
    """Act not found in the API."""

    def __init__(self, eli: str):
        super().__init__(f"Nie znaleziono aktu: {eli}", status_code=404)
        self.eli = eli


class ApiUnavailableError(SejmApiError):
    """API is temporarily unavailable."""

    pass


class ContentNotAvailableError(LawScrapperError):
    """Content not available for the specified format."""

    def __init__(self, eli: str, format: str):
        super().__init__(
            f"Treść niedostępna dla {eli} w formacie {format}"
        )
        self.eli = eli
        self.format = format


class DocumentNotLoadedError(LawScrapperError):
    """Document must be loaded before accessing content."""

    def __init__(self, eli: str):
        super().__init__(
            f"Dokument {eli} nie jest załadowany. "
            f"Użyj get_act_details(eli='{eli}', load_content=true)"
        )
        self.eli = eli


class InvalidEliError(LawScrapperError):
    """Invalid ELI identifier format."""

    def __init__(self, eli: str):
        super().__init__(
            f"Nieprawidłowy format ELI: {eli}. "
            f"Oczekiwany: wydawca/rok/pozycja (np. DU/2024/1716)"
        )
        self.eli = eli
