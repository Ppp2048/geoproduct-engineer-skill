"""Records: the entities a dataset is made of, with where they are and how sure we are.

Facts, estimates and inferences are told apart per attribute (``basis``), and resolved,
unresolved and conflicting records per record (``status``). Whether every fact actually
has evidence is checked by validation, which reports problems rather than refusing to load.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, JsonValue, StringConstraints, model_validator

from geoproduct.schemas._base import (
    SCHEMA_VERSION,
    Contract,
    Fraction,
    Identifier,
    SchemaVersion,
    ShortText,
    Text,
)
from geoproduct.schemas.geometry import Geometry


class Basis(StrEnum):
    fact = "fact"
    """Stated by a source; must be backed by evidence."""
    estimate = "estimate"
    """Calculated or modelled, e.g. a height from a floor count."""
    inference = "inference"
    """Concluded by reasoning from other facts; say how in ``note``."""


class AttributeValue(Contract):
    value: JsonValue = None
    basis: Basis = Basis.fact
    evidence_ids: list[Identifier] = Field(default_factory=list)
    note: Text | None = None


class Address(Contract):
    full: Text | None = None
    house_number: ShortText | None = None
    street: ShortText | None = None
    locality: ShortText | None = None
    region: ShortText | None = None
    postcode: ShortText | None = None
    country_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None = None
    """ISO 3166-1 alpha-2, upper case."""


class LocationKind(StrEnum):
    building_footprint = "building_footprint"
    entrance = "entrance"
    address_point = "address_point"
    centroid = "centroid"
    approximate = "approximate"
    unknown = "unknown"


class Location(Contract):
    """What the geometry represents and how precise it is."""

    kind: LocationKind = LocationKind.unknown
    precision_m: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    method: ShortText | None = None
    """How it was obtained, e.g. ``geocode:nominatim``, ``osm:way``, ``file``."""
    evidence_ids: list[Identifier] = Field(default_factory=list)


class MatchStatus(StrEnum):
    exact = "exact"
    """Inside the footprint and confirmed by a name, address or identifier."""
    probable = "probable"
    """One plausible footprint, but without confirming evidence."""
    ambiguous = "ambiguous"
    """Several footprints are similarly plausible; none is chosen."""
    approximate_point = "approximate_point"
    """No footprint can be chosen safely; the point location stands on its own."""
    none = "none"
    """No candidate footprint."""


class BuildingRef(Contract):
    provider: ShortText
    id: Identifier


class BuildingCandidate(Contract):
    ref: BuildingRef
    distance_m: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    contains_point: bool
    name_similarity: Fraction | None = None
    score: Fraction
    reasons: list[Text] = Field(default_factory=list)


class BuildingMatch(Contract):
    status: MatchStatus
    building_ref: BuildingRef | None = None
    method: ShortText
    score: Fraction | None = None
    reasons: Annotated[list[Text], Field(min_length=1)]
    """Every decision is explained, including a decision not to match."""
    candidates: list[BuildingCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "BuildingMatch":
        chosen = self.status in (MatchStatus.exact, MatchStatus.probable)
        if chosen and self.building_ref is None:
            raise ValueError(f"a '{self.status}' match must name its building_ref")
        if not chosen and self.building_ref is not None:
            raise ValueError(f"a '{self.status}' match must not name a building_ref")
        if self.status == MatchStatus.ambiguous and len(self.candidates) < 2:
            raise ValueError("an 'ambiguous' match must list at least two candidates")
        return self


class ConfidenceLevel(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"
    unresolved = "unresolved"


class Confidence(Contract):
    level: ConfidenceLevel
    score: Fraction | None = None
    reasons: list[Text] = Field(default_factory=list)


class RecordStatus(StrEnum):
    resolved = "resolved"
    unresolved = "unresolved"
    conflicting = "conflicting"
    rejected = "rejected"


class ConflictingValue(Contract):
    value: JsonValue = None
    evidence_ids: list[Identifier] = Field(default_factory=list)


class Conflict(Contract):
    """Sources disagree about one field. Values are kept side by side, never overwritten."""

    field: ShortText
    values: Annotated[list[ConflictingValue], Field(min_length=2)]
    note: Text | None = None


class Merge(Contract):
    """Another record folded into this one by deduplication, and why."""

    record_id: Identifier
    score: Fraction
    reasons: Annotated[list[Text], Field(min_length=1)]


class Record(Contract):
    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    name: ShortText | None = None
    aliases: list[ShortText] = Field(default_factory=list)
    category: ShortText | None = None
    organisation_id: Identifier | None = None
    """Links several sites of one organisation; each site stays its own record."""
    external_ids: dict[ShortText, ShortText] = Field(default_factory=dict)
    """Identifiers in other systems, e.g. ``{"osm": "way/123", "website": "..."}``."""
    address: Address | None = None
    attributes: dict[ShortText, AttributeValue] = Field(default_factory=dict)
    geometry: Geometry | None = None
    location: Location = Field(default_factory=Location)
    building_match: BuildingMatch | None = None
    evidence_ids: list[Identifier] = Field(default_factory=list)
    """Evidence that the entity exists and is what the record says it is."""
    confidence: Confidence | None = None
    status: RecordStatus = RecordStatus.unresolved
    conflicts: list[Conflict] = Field(default_factory=list)
    merged: list[Merge] = Field(default_factory=list)
    limitations: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def _conflicts_match_status(self) -> "Record":
        if self.status == RecordStatus.conflicting and not self.conflicts:
            raise ValueError("a 'conflicting' record must list its conflicts")
        return self

    def to_feature(self) -> dict[str, Any]:
        """The record as a GeoJSON Feature; everything except id and geometry goes in properties."""
        properties = self.model_dump(mode="json", exclude_none=True)
        record_id = properties.pop("id")
        geometry = properties.pop("geometry", None)
        return {"type": "Feature", "id": record_id, "geometry": geometry, "properties": properties}

    @classmethod
    def from_feature(cls, feature: Mapping[str, Any]) -> "Record":
        if feature.get("type") != "Feature":
            raise ValueError("not a GeoJSON Feature")
        properties = dict(feature.get("properties") or {})
        record_id = feature.get("id", properties.get("id"))
        return cls.model_validate(
            {**properties, "id": record_id, "geometry": feature.get("geometry")}
        )
