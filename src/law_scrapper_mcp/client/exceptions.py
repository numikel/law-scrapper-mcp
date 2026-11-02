"""Exception hierarchy for Law Scrapper MCP client."""

from __future__ import annotations

# Name under which errors raised for api.sejm.gov.pl identify their source. Error texts for this
# source predate source naming and must stay byte-identical, so the tool layer treats it (and a
# missing source) as "legacy" and leaves them alone.
SEJM_SOURCE = "api.sejm.gov.pl"


class LawScrapperError(Exception):
    """Base exception for Law Scrapper MCP errors."""

    pass


class SejmApiError(LawScrapperError):
    """Error from an upstream source (historically only the Sejm API).

    `source` is the label of the host that failed (`EgressHost.name`); `None` means the error was
    not raised by an egress client. The tool layer uses it to name the source in the message.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
        *,
        source: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.source = source


class ActNotFoundError(SejmApiError):
    """Act not found in the API."""

    def __init__(self, eli: str, *, source: str | None = None):
        super().__init__(f"Nie znaleziono aktu: {eli}", status_code=404, source=source)
        self.eli = eli


class ApiUnavailableError(SejmApiError):
    """API is temporarily unavailable.

    Covers the whole 5xx range, transport errors and rejection by an open
    circuit breaker — exactly the events the breaker counts as a failure.
    Inherits from `SejmApiError`, so existing `except SejmApiError` blocks catch
    them just as before.
    """


class EgressRedirectRefusedError(LawScrapperError):
    """A source answered with a redirect to another origin, and the client refused to follow it.

    Deliberately not an `ApiUnavailableError`: retrying cannot help, because the address the
    module is configured with is what has to change. Raised from inside the request pipeline, so
    it is neither retried nor booked as a breaker failure. Carries the label of the source.
    """

    def __init__(self, message: str, *, source: str | None = None):
        super().__init__(message)
        self.source = source


class EgressConfigError(LawScrapperError):
    """An egress host or caller description is invalid, or claims something already claimed.

    Raised at startup, while modules register their hosts, so an operator sees it before any
    request is sent.
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

    `exact=False` marks a size measured on a download aborted at the budget:
    the body is known to be *at least* that large, and the message says so
    rather than quoting a running total as the document's size.
    """

    @staticmethod
    def _measured(size_bytes: int, *, exact: bool) -> str:
        """Render a size that may be a lower bound rather than a total."""
        return f"{size_bytes} B" if exact else f"co najmniej {size_bytes} B"

    def __init__(self, eli: str, size_bytes: int, limit_bytes: int, pdf_url: str | None = None, *, exact: bool = True):
        measured = self._measured(size_bytes, exact=exact)
        message = f"Treść aktu {eli} ma {measured} i przekracza limit {limit_bytes} B, więc nie została przetworzona."
        if pdf_url is not None:
            message += f" Pobierz plik źródłowy: {pdf_url}"
        super().__init__(message)
        self.eli = eli
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        self.pdf_url = pdf_url
        self.exact = exact


class ResponseTooLargeError(ContentTooLargeError):
    """A response body ran past its byte budget while it was still streaming in.

    Raised by the client, which knows the URL and the budget but neither the act
    nor the source file an agent should fetch instead; `ActService` re-raises it
    as a full `ContentTooLargeError` carrying that context. Subclassed so that a
    refusal which slips through untranslated still lands in the same tool error
    category as the post-hoc size gates, instead of falling through as an
    internal error.

    `exact` is `True` when the size came from `Content-Length` and the body was
    never read, `False` when the download was aborted at `size_bytes` — the
    body is then known only to be at least that large.
    """

    def __init__(self, url: str, size_bytes: int, limit_bytes: int, *, exact: bool):
        # The parent's message is shaped around an act; this one has only a URL,
        # so the base class is initialised directly with a message of its own.
        measured = self._measured(size_bytes, exact=exact)
        LawScrapperError.__init__(
            self,
            f"Odpowiedź z {url} ma {measured} i przekracza limit {limit_bytes} B, więc pobieranie przerwano.",
        )
        self.url = url
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        self.exact = exact
        # Every `ContentTooLargeError` promises these two, and being a subclass is the
        # whole point of this class — a refusal that slips through untranslated must
        # land in the same handler. They are present and empty, not absent, so that
        # handler reads "no act context here" instead of raising `AttributeError`.
        self.eli = ""
        self.pdf_url = None


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
