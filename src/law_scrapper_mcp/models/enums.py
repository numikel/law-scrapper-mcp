"""Enumeration types for Law Scrapper MCP."""

from __future__ import annotations

from enum import StrEnum


class Publisher(StrEnum):
    """Polish legal act publishers."""

    DU = "DU"
    MP = "MP"

    @property
    def label(self) -> str:
        """Human-readable label for the publisher."""
        labels = {
            Publisher.DU: "Dziennik Ustaw",
            Publisher.MP: "Monitor Polski",
        }
        return labels[self]


class DetailLevel(StrEnum):
    """Level of detail for search and browse results."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"


class MetadataCategory(StrEnum):
    """Categories of metadata available from the API."""

    KEYWORDS = "keywords"
    PUBLISHERS = "publishers"
    STATUSES = "statuses"
    TYPES = "types"
    INSTITUTIONS = "institutions"
    ALL = "all"


class ContentFormat(StrEnum):
    """Format for legal act content retrieval."""

    HTML = "html"
    PDF = "pdf"


class RelationshipType(StrEnum):
    """Types of relationships between legal acts."""

    CHANGED_ACTS = "Akty zmienione"
    REPEALED_ACTS = "Akty uchylone"
    DEEMED_REPEALED_ACTS = "Akty uznane za uchylone"
    LEGAL_BASIS = "Podstawa prawna"
    AMENDING_ACTS = "Akty zmieniające"
    REPEALING_ACTS = "Akty uchylające"
    CONSOLIDATED_TEXTS = "Teksty jednolite"
