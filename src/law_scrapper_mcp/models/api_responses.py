"""Pydantic models for Sejm API responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ActSummary(BaseModel):
    """Summary information about a legal act."""

    model_config = ConfigDict(extra="ignore")

    ELI: str
    publisher: str
    year: int
    pos: int
    title: str
    status: str
    type: str | None = None
    promulgation: str | None = None
    dateEffect: str | None = None
    inForce: str | None = None


class ActDetail(ActSummary):
    """Detailed information about a legal act."""

    announcementDate: str | None = None
    entryIntoForce: str | None = None
    validFrom: str | None = None
    repealDate: str | None = None
    changeDate: str | None = None
    keywords: list[str] = []
    references: dict[str, Any] | None = None
    volume: int | None = None
    textPDF: str | None = None
    textHTML: str | None = None


class StructureNode(BaseModel):
    """Node in the table of contents structure."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None
    type: str | None = None
    children: list[StructureNode] = []


class ActReference(BaseModel):
    """Reference to another legal act."""

    model_config = ConfigDict(extra="ignore")

    act: ActSummary | None = None
    date: str | None = None
    description: str | None = None


class SearchApiResponse(BaseModel):
    """Response from the search API endpoint."""

    model_config = ConfigDict(extra="ignore")

    count: int
    items: list[ActSummary]


class PublisherInfo(BaseModel):
    """Information about a publisher."""

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    shortName: str | None = None
    actsCount: int | None = None
    years: list[int] = []
