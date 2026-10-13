from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import re

from .enums import ReconstructionStatus
from .geometry import AuthoritativeInputGeometry, ModelValidationError, require_nonempty
from .records import (
    AssetReference,
    Component,
    CostMetrics,
    CostTelemetry,
    CoverageRecord,
    EvidenceRecord,
    ExplicitFallbackRecord,
    FailClosedRecord,
    Relation,
    RendererRealization,
    RepresentationDecision,
    ReviewItem,
    RevisionRecord,
    SemanticGroup,
)


SCHEMA_VERSION = "0.1"


def make_stable_id(
    figure_id: str,
    object_kind: str,
    stable_sequence: int,
    semantic_key: str = "",
) -> str:
    """Create a deterministic ID that deliberately excludes geometry."""
    require_nonempty(figure_id, "figure_id")
    require_nonempty(object_kind, "object_kind")
    if stable_sequence < 0:
        raise ModelValidationError("stable_sequence must be nonnegative")
    kind = re.sub(r"[^a-z0-9]+", "-", object_kind.lower()).strip("-")
    digest = sha256(
        f"{figure_id}\x1f{kind}\x1f{stable_sequence}\x1f{semantic_key}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{kind}-{stable_sequence:04d}-{digest}"


@dataclass(frozen=True, slots=True)
class CanonicalFigure:
    schema_version: str
    figure_id: str
    input_geometry: AuthoritativeInputGeometry
    components: tuple[Component, ...]
    groups: tuple[SemanticGroup, ...]
    relations: tuple[Relation, ...]
    assets: tuple[AssetReference, ...]
    representation_decisions: tuple[RepresentationDecision, ...]
    fallbacks: tuple[ExplicitFallbackRecord, ...]
    coverage: tuple[CoverageRecord, ...]
    review_items: tuple[ReviewItem, ...]
    failures: tuple[FailClosedRecord, ...]
    revision: RevisionRecord
    evidence: tuple[EvidenceRecord, ...]
    cost_telemetry: CostTelemetry
    renderer_realizations: tuple[RendererRealization, ...]
    reconstruction_status: ReconstructionStatus
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        order = {
            "components": "component_id",
            "groups": "group_id",
            "relations": "relation_id",
            "assets": "asset_id",
            "representation_decisions": "decision_id",
            "fallbacks": "fallback_id",
            "coverage": "coverage_id",
            "review_items": "review_id",
            "failures": "failure_id",
            "evidence": "evidence_id",
            "renderer_realizations": "realization_id",
        }
        for field_name, key_name in order.items():
            object.__setattr__(
                self,
                field_name,
                tuple(sorted(getattr(self, field_name), key=lambda item: getattr(item, key_name))),
            )

    @classmethod
    def empty(
        cls,
        figure_id: str,
        input_geometry: AuthoritativeInputGeometry,
        revision: RevisionRecord,
    ) -> "CanonicalFigure":
        return cls(
            schema_version=SCHEMA_VERSION,
            figure_id=figure_id,
            input_geometry=input_geometry,
            components=(),
            groups=(),
            relations=(),
            assets=(),
            representation_decisions=(),
            fallbacks=(),
            coverage=(),
            review_items=(),
            failures=(),
            revision=revision,
            evidence=(),
            cost_telemetry=CostTelemetry(CostMetrics(), CostMetrics()),
            renderer_realizations=(),
            reconstruction_status=ReconstructionStatus.IN_PROGRESS,
            metadata={},
        )

    def validate(self) -> None:
        from unified_figure.qa.validators import validate_canonical_figure

        validate_canonical_figure(self)

    def to_dict(self) -> dict[str, object]:
        from unified_figure.serialization.canonical import canonical_dict

        return canonical_dict(self)

    def to_json(self) -> str:
        from unified_figure.serialization.canonical import canonical_json

        return canonical_json(self)

    def state_hash(self) -> str:
        from unified_figure.serialization.canonical import canonical_state_hash

        return canonical_state_hash(self)

    def with_computed_revision_hash(self) -> "CanonicalFigure":
        return replace(
            self,
            revision=replace(self.revision, canonical_state_hash=self.state_hash()),
        )

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CanonicalFigure":
        from unified_figure.serialization.canonical import from_primitive

        value = from_primitive(cls, data)
        value.validate()
        return value

    @classmethod
    def from_json(cls, text: str) -> "CanonicalFigure":
        from unified_figure.serialization.canonical import canonical_loads

        return canonical_loads(text)
