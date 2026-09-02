"""Exception hierarchy for Law Scrapper MCP client."""

from __future__ import annotations


class LawScrapperError(Exception):
    """Base exception for Law Scrapper MCP errors."""

    pass


class SejmApiError(LawScrapperError):
    """Error from Sejm API."""

    def __init__(self, message: str, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class ActNotFoundError(SejmApiError):
    """Act not found in the API."""

    def __init__(self, eli: str):
        super().__init__(f"Nie znaleziono aktu: {eli}", status_code=404)
        self.eli = eli


class ApiUnavailableError(SejmApiError):
    """API is temporarily unavailable.

    Covers the whole 5xx range, transport errors and rejection by an open
    circuit breaker — exactly the events the breaker counts as a failure.
    Inherits from `SejmApiError`, so existing `except SejmApiError` blocks catch
    them just as before.
    """


class ContentNotAvailableError(LawScrapperError):
    """Content not available for the specified format."""

    def __init__(self, eli: str, format: str):
        super().__init__(f"Treść niedostępna dla {eli} w formacie {format}")
        self.eli = eli
        self.format = format


class ContentTooLargeError(LawScrapperError):
    """Fetched content exceeds the conversion size limit.

    Refusal, not truncation: a legal act cut mid-clause is a silent loss the
    model cannot detect, while a refusal naming the source URL is one it can
    act on. The message is Polish because the agent reads it.
    """

    def __init__(self, eli: str, size_bytes: int, limit_bytes: int, pdf_url: str | None = None):
        message = (
            f"Treść aktu {eli} ma {size_bytes} B i przekracza limit {limit_bytes} B, więc nie została przetworzona."
        )
        if pdf_url is not None:
            message = f"{message} Pobierz plik źródłowy: {pdf_url}"
        super().__init__(message)
        self.eli = eli
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        self.pdf_url = pdf_url


class DocumentNotLoadedError(LawScrapperError):
    """Document must be loaded before accessing content."""

    def __init__(self, eli: str):
        super().__init__(f"Dokument {eli} nie jest załadowany. Użyj get_act_details(eli='{eli}', load_content=true)")
        self.eli = eli


class InvalidEliError(LawScrapperError):
    """Invalid ELI identifier format."""

    def __init__(self, eli: str):
        super().__init__(f"Nieprawidłowy format ELI: {eli}. Oczekiwany: wydawca/rok/pozycja (np. DU/2024/1716)")
        self.eli = eli
