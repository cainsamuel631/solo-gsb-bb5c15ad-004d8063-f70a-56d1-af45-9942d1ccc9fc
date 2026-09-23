"""Request/response models for the merge API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_serializer

# Any JSON document: object, array, string, number, boolean or null.
JsonValue = Optional[Any]

# Sides a conflict can be adjudicated to.
ResolutionSide = Literal["base", "left", "right"]

# Side a final value came from, per a provenance record.
ProvenanceSource = Literal["base", "left", "right", "merged"]


class ConflictResolution(BaseModel):
    """One adjudication: which side wins at a reported conflict."""

    model_config = ConfigDict(extra="forbid")

    pointer: str = Field(
        ...,
        description=(
            "JSON Pointer (RFC 6901) of a reported conflict, exactly as it "
            "appears in the response 'conflicts' (after identity-array "
            "indices are resolved)."
        ),
    )
    resolution: ResolutionSide = Field(
        ...,
        description=(
            "Side whose value is placed at the conflict location in the "
            "merged document. Choosing a side on which the location is "
            "deleted removes it; 'base' restores the default outcome."
        ),
    )


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base: JsonValue = Field(
        ..., description="Common ancestor of the two revisions."
    )
    left: JsonValue = Field(..., description="The left-hand revision.")
    right: JsonValue = Field(..., description="The right-hand revision.")
    array_identity: dict[str, str] = Field(
        default_factory=dict,
        alias="arrayIdentity",
        description=(
            "Mapping from a JSON Pointer (RFC 6901) of an object array to the "
            "name of the item field that uniquely identifies an entry. The "
            "token '*' matches a single positional index, e.g. "
            "'/servers/*/disks' -> 'name'."
        ),
    )
    conflict_resolutions: list[ConflictResolution] = Field(
        default_factory=list,
        alias="conflictResolutions",
        description=(
            "Adjudications for reported conflicts. Each entry binds a "
            "conflict pointer to the winning side; conflicts not listed keep "
            "the base value. Malformed, duplicated, or non-conflict pointers "
            "are rejected with HTTP 400. The response 'conflicts' always "
            "report the original three-way values (unless masked via "
            "'redactPaths')."
        ),
    )
    protected_paths: list[str] = Field(
        default_factory=list,
        alias="protectedPaths",
        description=(
            "JSON Pointers (RFC 6901) of locations that must never be "
            "written automatically. A single '*' token matches one path "
            "segment. When either revision changes a protected location - "
            "even a one-sided change or an identical change on both sides - "
            "the base value is kept and a conflict with kind "
            "'protected_change' carrying the three-way values is reported. "
            "Fields of objects and identity-array items match their final "
            "reported pointer; deletions match their original path. An "
            "explicit conflictResolutions entry for the reported pointer "
            "overrides the protection; unresolved protected conflicts keep "
            "the base value. Malformed or duplicate pointers are rejected "
            "with HTTP 400."
        ),
    )
    redact_paths: list[str] = Field(
        default_factory=list,
        alias="redactPaths",
        description=(
            "JSON Pointers (RFC 6901) of locations whose values are "
            "sensitive and must be masked in the response 'conflicts'. A "
            "single '*' token matches one path segment. A conflict "
            "reported at or below a listed pointer has its "
            "base/left/right values replaced with the fixed string "
            "'[REDACTED]'; a pointer reaching below a reported conflict "
            "is applied recursively inside the three-way snapshots. "
            "Sides on which the location is absent stay null. Redaction "
            "only rewrites the reported conflicts: 'merged', conflict "
            "detection and 'conflictResolutions' always use the original "
            "values. Malformed or duplicate pointers are rejected with "
            "HTTP 400."
        ),
    )
    include_provenance: StrictBool = Field(
        default=False,
        alias="includeProvenance",
        description=(
            "When true, the response additionally carries a 'provenance' "
            "array: one record per actual changed scalar leaf (or whole "
            "atomic array value) and per added/removed/whole-item "
            "adjudicated identity-array item, sorted by its final JSON "
            "Pointer. Each record names the side the final value came "
            "from ('base' when a conflict or protection kept the base "
            "value, 'left'/'right' for a single winning side, 'merged' "
            "for an identical two-sided change or a two-sided deletion), "
            "whether the outcome was produced by a conflict resolution "
            "('adjudicated'), and whether the unit is absent from the "
            "final document ('deleted'; the pointer keeps the original "
            "base location, and identity-array items use their original "
            "base index). Provenance stays consistent with protectedPaths, "
            "redactPaths and conflictResolutions and records no values, so "
            "redaction never masks or removes records. When false (the "
            "default) the response is exactly the unextended envelope."
        ),
    )


class Conflict(BaseModel):
    pointer: str = Field(..., description="JSON Pointer (RFC 6901) of the conflict.")
    kind: str = Field(
        ...,
        description=(
            "both_modified: both sides changed the value differently; "
            "delete_vs_modify: one side deleted what the other modified; "
            "both_added: both sides added different values at a path absent "
            "from base; protected_change: a protectedPaths location was "
            "changed by either revision (one-sided or identical changes "
            "included); the base value is kept until adjudicated."
        ),
    )
    base: JsonValue
    left: JsonValue
    right: JsonValue


class ProvenanceEntry(BaseModel):
    """Origin of one changed scalar leaf or identity-array item."""

    model_config = ConfigDict(extra="forbid")

    pointer: str = Field(
        ...,
        description=(
            "JSON Pointer (RFC 6901) of the changed unit in the final "
            "merged document. Identity-array items are addressed by their "
            "final index; a deleted item keeps its original base index."
        ),
    )
    source: ProvenanceSource = Field(
        ...,
        description=(
            "Where the final value came from: 'left' or 'right' is the "
            "single winning revision; 'merged' means both revisions "
            "arrived at the value together (an identical two-sided "
            "change, or a two-sided deletion); 'base' means a conflict or "
            "a protectedPaths location kept the base value."
        ),
    )
    adjudicated: bool = Field(
        ...,
        description=(
            "True when the final outcome at this pointer was produced by "
            "an explicit conflictResolutions entry (it differs from the "
            "unresolved default outcome); false otherwise."
        ),
    )
    deleted: bool = Field(
        ...,
        description=(
            "True when the unit is absent from the final merged document. "
            "Its pointer keeps the original base location (and an "
            "identity-array item its original base index)."
        ),
    )


class MergeResponse(BaseModel):
    merged: JsonValue
    conflicts: list[Conflict]
    provenance: Optional[list[ProvenanceEntry]] = Field(
        default=None,
        description=(
            "Present only when the request sets includeProvenance=true: "
            "the origin of every actual changed leaf or identity-array "
            "item, sorted by final JSON Pointer."
        ),
    )

    @model_serializer(mode="wrap")
    def _omit_provenance_unless_requested(self, handler):  # type: ignore[no-untyped-def]
        """Keep the envelope byte-identical when provenance is off."""
        result = handler(self)
        if self.provenance is None:
            result.pop("provenance", None)
        return result
