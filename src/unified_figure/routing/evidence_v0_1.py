from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageStat

from unified_figure.backends import BackendAvailability, BackendCapabilityRegistry
from unified_figure.model.enums import (
    AssetSourceType,
    CheckStatus,
    ComponentType,
    FidelityStatus,
    RepresentationType,
)
from unified_figure.model.figure import CanonicalFigure
from unified_figure.model.records import AssetReference, Component

from .fidelity_v0_2 import (
    BackendAvailabilitySnapshot,
    ComponentComplexity,
    EditabilityValue,
    EvidenceRating,
    ExpectedProfessionalValue,
    FidelityRoutingInput,
    GeneratedCandidateValue,
    ScientificCompatibility,
    ScientificSensitivity,
)


ROUTING_EVIDENCE_BUILDER_VERSION = "routing-evidence-builder-v0.2"


class EvidenceState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FieldDerivation:
    value: object
    state: EvidenceState
    source: str
    method: str
    confidence: str
    fallback_behavior: str

    def to_dict(self) -> dict[str, object]:
        value = self.value.value if isinstance(self.value, Enum) else self.value
        return {**asdict(self), "value": value, "state": self.state.value}


@dataclass(frozen=True, slots=True)
class BuiltRoutingEvidence:
    routing_input: FidelityRoutingInput
    field_derivations: Mapping[str, FieldDerivation]
    evidence_hash: str
    builder_version: str = ROUTING_EVIDENCE_BUILDER_VERSION

    def to_dict(self) -> dict[str, object]:
        value = asdict(self.routing_input)
        for key, item in tuple(value.items()):
            if isinstance(item, Enum):
                value[key] = item.value
        value["backend_availability"] = self.routing_input.backend_availability.to_dict()
        return {
            "builder_version": self.builder_version,
            "routing_input": value,
            "field_derivations": {
                key: self.field_derivations[key].to_dict() for key in sorted(self.field_derivations)
            },
            "evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class _AssetObservation:
    source_asset: AssetReference | None
    current_asset: AssetReference | None
    source_available: bool
    source_quality: EvidenceRating
    source_fidelity: EvidenceRating
    generated_compatibility: ScientificCompatibility
    reference_guidance_available: bool
    repairable_defects: tuple[str, ...]
    professional_value: ExpectedProfessionalValue
    source: str
    method: str


class RoutingEvidenceBuilder:
    """Build profile-independent fidelity evidence from canonical/runtime facts."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        backend_registry: BackendCapabilityRegistry,
        reference_path: str | Path | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.backend_registry = backend_registry
        self.reference_path = Path(reference_path).resolve() if reference_path is not None else None

    def build_all(self, figure: CanonicalFigure) -> tuple[BuiltRoutingEvidence, ...]:
        figure.validate()
        return tuple(self.build(figure, component) for component in figure.components)

    def build(self, figure: CanonicalFigure, component: Component) -> BuiltRoutingEvidence:
        assets = {item.asset_id: item for item in figure.assets}
        observation = self._asset_observation(figure, component, assets)
        complexity = self._complexity(figure, component)
        sensitivity = self._scientific_sensitivity(figure, component)
        native_fidelity = self._native_fidelity(component)
        editability = self._editability(component)
        generated_value = self._generated_value(
            complexity, sensitivity, observation.source_quality,
            observation.source_fidelity, native_fidelity,
        )
        availability = self._availability()
        derivations = self._derivations(
            figure, component, observation, complexity, sensitivity,
            native_fidelity, editability, generated_value, availability,
        )
        unknown_fields = tuple(
            sorted(
                key for key, derivation in derivations.items()
                if derivation.state is EvidenceState.UNKNOWN
            )
        )
        decision_critical_unknowns = self._decision_critical_unknowns(
            component=component,
            observation=observation,
            complexity=complexity,
            native_fidelity=native_fidelity,
            generated_value=generated_value,
            availability=availability,
            unknown_fields=unknown_fields,
        )
        routing_input = FidelityRoutingInput(
            component_id=component.component_id,
            component_type=component.component_type,
            component_complexity=complexity,
            scientific_sensitivity=sensitivity,
            source_available=observation.source_available,
            source_asset_quality=observation.source_quality,
            source_preservation_fidelity=observation.source_fidelity,
            native_realization_fidelity=native_fidelity,
            editability_value=editability,
            generated_asset_candidate_value=generated_value,
            generated_scientific_compatibility=observation.generated_compatibility,
            reference_guidance_available=observation.reference_guidance_available,
            professional_refinement_expected_value=observation.professional_value,
            repairable_defects=observation.repairable_defects,
            backend_availability=availability,
            source_preservation_ready=observation.source_asset is not None,
            unknown_fields=unknown_fields,
            decision_critical_unknowns=decision_critical_unknowns,
        )
        payload = repr(
            tuple((key, derivations[key].to_dict()) for key in sorted(derivations))
        ).encode("utf-8")
        return BuiltRoutingEvidence(
            routing_input=routing_input,
            field_derivations=derivations,
            evidence_hash="sha256:" + sha256(payload).hexdigest(),
        )

    @staticmethod
    def _decision_critical_unknowns(
        *,
        component: Component,
        observation: _AssetObservation,
        complexity: ComponentComplexity,
        native_fidelity: EvidenceRating,
        generated_value: GeneratedCandidateValue,
        availability: BackendAvailabilitySnapshot,
        unknown_fields: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return only UNKNOWNs capable of changing the safe representation choice."""

        unknown = set(unknown_fields)
        critical: set[str] = set()
        if "native_realization_fidelity" in unknown:
            critical.add("native_realization_fidelity")
        if (
            observation.source_available
            and complexity is ComponentComplexity.HIGH
            and native_fidelity not in {EvidenceRating.HIGH, EvidenceRating.ADEQUATE}
        ):
            critical.update(
                unknown & {"source_asset_quality", "source_preservation_fidelity"}
            )
        if (
            availability.image_generation
            and generated_value is not GeneratedCandidateValue.NONE
        ):
            critical.update(unknown & {"generated_scientific_compatibility"})
        if availability.photoshop_refinement and observation.repairable_defects:
            critical.update(unknown & {"professional_refinement_expected_value"})
        if component.component_type is ComponentType.UNKNOWN:
            critical.update(unknown & {"native_realization_fidelity"})
        return tuple(sorted(critical))

    def source_fallback_asset_id(self, figure: CanonicalFigure, component: Component) -> str | None:
        assets = {item.asset_id: item for item in figure.assets}
        current = assets.get(component.asset_ref or "")
        if current is not None and current.source_type is not AssetSourceType.SOURCE_GENERATED:
            return current.asset_id
        explicit = figure.metadata.get(f"fallback_asset.{component.component_id}")
        if explicit is None and figure.metadata.get("phase6d.generated_asset_component") == component.component_id:
            explicit = figure.metadata.get("phase6d.generated_asset_fallback")
        if explicit is None and figure.metadata.get("phase6d2.professional_refinement_component") == component.component_id:
            explicit = figure.metadata.get("phase6d2.professional_refinement_fallback")
        if explicit in assets:
            return explicit
        if current is None:
            return None
        for fallback in figure.fallbacks:
            if fallback.component_id != component.component_id:
                continue
            decision = next(
                (item for item in figure.representation_decisions if item.decision_id == fallback.fallback_decision_id),
                None,
            )
            if decision is None:
                continue
            for asset in figure.assets:
                if asset.asset_id != current.asset_id and asset.source_region == current.source_region:
                    return asset.asset_id
        return None

    def _asset_observation(
        self,
        figure: CanonicalFigure,
        component: Component,
        assets: Mapping[str, AssetReference],
    ) -> _AssetObservation:
        current = assets.get(component.asset_ref or "")
        fallback_id = self.source_fallback_asset_id(figure, component)
        source_asset = assets.get(fallback_id or "") or (
            current if current is not None and current.source_type is not AssetSourceType.SOURCE_GENERATED else None
        )
        generated_compatibility = ScientificCompatibility.NOT_EVALUATED
        if current is not None and current.source_type is AssetSourceType.SOURCE_GENERATED:
            review = current.scientific_review
            if review is not None and review.has_deviation():
                generated_compatibility = ScientificCompatibility.FAIL
            elif review is not None and all(
                value is CheckStatus.PASS
                for value in (
                    review.scientific_content_review,
                    review.component_count_check,
                    review.topology_check,
                    review.symbol_check,
                    review.orientation_check,
                    review.color_encoding_check,
                )
            ):
                generated_compatibility = ScientificCompatibility.PASS

        if source_asset is not None:
            path = self._resolve_file(source_asset.file_ref)
            quality, method, defects = self._inspect_source_path(path, component)
            source_fidelity = (
                EvidenceRating.SOURCE_EXACT
                if quality is EvidenceRating.HIGH
                and source_asset.scientific_fidelity_status is FidelityStatus.PRESERVED
                else EvidenceRating.ADEQUATE
                if quality is EvidenceRating.ADEQUATE
                else EvidenceRating.INADEQUATE
                if quality is EvidenceRating.INADEQUATE
                else EvidenceRating.UNKNOWN
            )
            professional = (
                ExpectedProfessionalValue.CLEAR
                if figure.metadata.get("phase6d2.professional_refinement_component") == component.component_id
                else ExpectedProfessionalValue.UNCERTAIN
                if defects
                else ExpectedProfessionalValue.NONE
            )
            return _AssetObservation(
                source_asset, current, path is not None, quality, source_fidelity,
                generated_compatibility, path is not None, defects, professional,
                source_asset.asset_id,
                method,
            )

        reference = self._verified_reference(figure)
        if reference is not None:
            quality, method, defects = self._inspect_reference_region(reference, component)
            return _AssetObservation(
                None, current, True, quality,
                EvidenceRating.SOURCE_EXACT if quality is EvidenceRating.HIGH else quality,
                generated_compatibility, True, defects,
                ExpectedProfessionalValue.UNCERTAIN if defects else ExpectedProfessionalValue.NONE,
                figure.input_geometry.source_hash, method,
            )
        return _AssetObservation(
            None, current, False, EvidenceRating.UNKNOWN, EvidenceRating.UNKNOWN,
            generated_compatibility, False, (), ExpectedProfessionalValue.NONE,
            "canonical component has no bound source asset/reference",
            "routing-evidence-builder-v0.1:no-source",
        )

    def _resolve_file(self, file_ref: str | None) -> Path | None:
        if not file_ref:
            return None
        path = (self.repository_root / file_ref).resolve()
        if self.repository_root not in path.parents or not path.is_file():
            return None
        return path

    def _verified_reference(self, figure: CanonicalFigure) -> Path | None:
        path = self.reference_path
        if path is None or not path.is_file():
            return None
        digest = "sha256:" + sha256(path.read_bytes()).hexdigest()
        return path if digest == figure.input_geometry.source_hash else None

    @staticmethod
    def _image_quality(image: Image.Image, component: Component) -> tuple[EvidenceRating, tuple[str, ...]]:
        rgb = image.convert("RGB")
        stat = ImageStat.Stat(rgb)
        variance = sum(stat.var) / max(len(stat.var), 1)
        width, height = rgb.size
        expected = component.geometry.bbox
        effective_scale = min(width / max(expected.width, 1.0), height / max(expected.height, 1.0))
        defects: list[str] = []
        if variance < 1.0:
            return EvidenceRating.INADEQUATE, ()
        if width > 2 and height > 2:
            def pixels_of(value: Image.Image) -> list[tuple[int, int, int]]:
                getter = getattr(value, "get_flattened_data", value.getdata)
                return list(getter())
            pixels = pixels_of(rgb.crop((0, 0, width, 1)))
            pixels += pixels_of(rgb.crop((0, height - 1, width, height)))
            pixels += pixels_of(rgb.crop((0, 0, 1, height)))
            pixels += pixels_of(rgb.crop((width - 1, 0, width, height)))
            white_edge = sum(all(channel >= 245 for channel in pixel) for pixel in pixels) / max(len(pixels), 1)
            if white_edge >= 0.95:
                defects.append("ALPHA_BACKGROUND")
        if min(width, height) < 96 or width * height < 20_000 or effective_scale < 0.75:
            return EvidenceRating.INADEQUATE, tuple(defects)
        if effective_scale < 1.0 or min(width, height) < 160:
            return EvidenceRating.ADEQUATE, tuple(defects)
        return EvidenceRating.HIGH, tuple(defects)

    def _inspect_source_path(
        self, path: Path | None, component: Component
    ) -> tuple[EvidenceRating, str, tuple[str, ...]]:
        if path is None:
            return EvidenceRating.UNKNOWN, "asset file unavailable", ()
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                quality, defects = self._image_quality(image, component)
            return quality, "deterministic asset dimensions/variance/effective-scale/edge QA", defects
        except (OSError, ValueError):
            return EvidenceRating.INADEQUATE, "deterministic asset decode failed", ()

    def _inspect_reference_region(
        self, path: Path, component: Component
    ) -> tuple[EvidenceRating, str, tuple[str, ...]]:
        bbox = component.geometry.bbox
        try:
            with Image.open(path) as image:
                x0 = max(0, round(bbox.x)); y0 = max(0, round(bbox.y))
                x1 = min(image.width, round(bbox.x + bbox.width)); y1 = min(image.height, round(bbox.y + bbox.height))
                if x1 <= x0 or y1 <= y0:
                    return EvidenceRating.INADEQUATE, "component bbox has no source pixels", ()
                quality, defects = self._image_quality(image.crop((x0, y0, x1, y1)), component)
            return quality, "authoritative hash-bound source-region dimensions/variance/edge QA", defects
        except OSError:
            return EvidenceRating.INADEQUATE, "authoritative source decode failed", ()

    @staticmethod
    def _complexity(figure: CanonicalFigure, component: Component) -> ComponentComplexity:
        if component.component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.CHART, ComponentType.IMAGE_ASSET}:
            return ComponentComplexity.HIGH
        relations = sum(
            component.component_id in {item.source_component_id, item.target_component_id}
            for item in figure.relations
        )
        groups = sum(component.component_id in item.member_component_ids for item in figure.groups)
        if component.component_type is ComponentType.UNKNOWN or relations >= 3 or groups >= 2:
            return ComponentComplexity.MODERATE
        return ComponentComplexity.SIMPLE

    @staticmethod
    def _scientific_sensitivity(figure: CanonicalFigure, component: Component) -> ScientificSensitivity:
        if component.component_type in {ComponentType.CHART, ComponentType.IMAGE_ASSET, ComponentType.COMPLEX_VISUAL}:
            return ScientificSensitivity.HIGH if component.component_type is not ComponentType.COMPLEX_VISUAL else ScientificSensitivity.MEDIUM
        topology = any(
            component.component_id in {item.source_component_id, item.target_component_id}
            for item in figure.relations
        )
        return ScientificSensitivity.MEDIUM if topology else ScientificSensitivity.LOW

    @staticmethod
    def _native_fidelity(component: Component) -> EvidenceRating:
        if (
            component.asset_ref
            and component.representation
            in {
                RepresentationType.EXACT_RASTER_ASSET,
                RepresentationType.RASTER_PRESERVE_COMPLEX,
            }
        ):
            # A bound, already-validated source asset is affirmative evidence
            # that this component carries visual content beyond its coarse
            # semantic type (notably PANEL).  Treating PANEL alone as adequate
            # native evidence discards the Phase 6C preservation decision.
            return EvidenceRating.INADEQUATE
        if component.component_type is ComponentType.TEXT:
            return EvidenceRating.HIGH
        if component.component_type in {ComponentType.SIMPLE_SHAPE, ComponentType.RELATION_ANCHOR, ComponentType.PANEL}:
            return EvidenceRating.ADEQUATE
        if component.component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.CHART, ComponentType.IMAGE_ASSET}:
            return EvidenceRating.INADEQUATE
        return EvidenceRating.UNKNOWN

    @staticmethod
    def _editability(component: Component) -> EditabilityValue:
        if component.component_type in {ComponentType.TEXT, ComponentType.SIMPLE_SHAPE, ComponentType.RELATION_ANCHOR}:
            return EditabilityValue.HIGH
        if component.component_type in {ComponentType.PANEL, ComponentType.CHART}:
            return EditabilityValue.MEDIUM
        return EditabilityValue.LOW

    @staticmethod
    def _generated_value(
        complexity: ComponentComplexity,
        sensitivity: ScientificSensitivity,
        source_quality: EvidenceRating,
        source_fidelity: EvidenceRating,
        native_fidelity: EvidenceRating,
    ) -> GeneratedCandidateValue:
        if source_fidelity in {EvidenceRating.HIGH, EvidenceRating.SOURCE_EXACT}:
            return GeneratedCandidateValue.NONE
        if complexity is not ComponentComplexity.HIGH or native_fidelity is not EvidenceRating.INADEQUATE:
            return GeneratedCandidateValue.NONE
        if source_quality is EvidenceRating.INADEQUATE and sensitivity is not ScientificSensitivity.HIGH:
            return GeneratedCandidateValue.CLEAR
        return GeneratedCandidateValue.UNCERTAIN

    def _availability(self) -> BackendAvailabilitySnapshot:
        def available(backend_id: str) -> bool:
            try:
                return self.backend_registry.get(backend_id).availability is BackendAvailability.AVAILABLE
            except KeyError:
                return False
        return BackendAvailabilitySnapshot(
            pptx_native=available("PPTX_NATIVE"),
            svg_native=available("SVG_NATIVE"),
            exact_source_preserve=available("EXACT_SOURCE_PRESERVE"),
            image_generation=available("IMAGE_GENERATION"),
            photoshop_refinement=available("PHOTOSHOP_REFINEMENT"),
        )

    def _derivations(
        self,
        figure: CanonicalFigure,
        component: Component,
        observation: _AssetObservation,
        complexity: ComponentComplexity,
        sensitivity: ScientificSensitivity,
        native_fidelity: EvidenceRating,
        editability: EditabilityValue,
        generated_value: GeneratedCandidateValue,
        availability: BackendAvailabilitySnapshot,
    ) -> dict[str, FieldDerivation]:
        known = EvidenceState.KNOWN
        unavailable = EvidenceState.UNAVAILABLE
        return {
            "component_complexity": FieldDerivation(complexity, known, "canonical component/groups/relations", ROUTING_EVIDENCE_BUILDER_VERSION, "BOUNDED_RULE", "conservative MODERATE/HIGH"),
            "scientific_sensitivity": FieldDerivation(sensitivity, known, "canonical semantic type/topology", ROUTING_EVIDENCE_BUILDER_VERSION, "CONSERVATIVE", "generation requires stronger acceptance"),
            "source_asset_quality": FieldDerivation(observation.source_quality, known if observation.source_available else EvidenceState.UNKNOWN, observation.source, observation.method, "DETERMINISTIC_QA", "safe native/source review"),
            "source_preservation_fidelity": FieldDerivation(observation.source_fidelity, known if observation.source_available else EvidenceState.UNKNOWN, observation.source, observation.method, "DETERMINISTIC_QA", "do not claim high fidelity"),
            "native_realization_fidelity": FieldDerivation(native_fidelity, known if native_fidelity is not EvidenceRating.UNKNOWN else EvidenceState.UNKNOWN, component.component_type.value, "renderer capability match v0.1", "BOUNDED_RULE", "source preserve or review"),
            "editability_value": FieldDerivation(editability, known, component.component_type.value, "semantic editability policy v0.1", "BOUNDED_RULE", "never optimize native percentage"),
            "generated_asset_candidate_value": FieldDerivation(generated_value, known, "derived fidelity gap/sensitivity", "generation candidacy evidence rule v0.1", "BOUNDED_RULE", "source preserve"),
            "generated_scientific_compatibility": FieldDerivation(observation.generated_compatibility, known if observation.generated_compatibility is not ScientificCompatibility.NOT_EVALUATED else EvidenceState.UNKNOWN, "bound generated asset scientific review", ROUTING_EVIDENCE_BUILDER_VERSION, "REVIEW_BOUND", "reject or do not accept generation"),
            "professional_refinement_expected_value": FieldDerivation(observation.professional_value, known if observation.professional_value is not ExpectedProfessionalValue.UNCERTAIN else EvidenceState.UNKNOWN, observation.source, "bounded alpha/accepted-refinement evidence", "DETERMINISTIC_QA", "avoid Photoshop"),
            "source_preservation_ready": FieldDerivation(observation.source_asset is not None, known, observation.source, ROUTING_EVIDENCE_BUILDER_VERSION, "EXACT", "review when no bound exact-source realization exists"),
            "backend_availability": FieldDerivation(availability.to_dict(), known if any(availability.to_dict().values()) else unavailable, self.backend_registry.fingerprint(), "BackendCapabilityRegistry runtime snapshot", "EXACT", "base safe route or review"),
            "profile_fields": FieldDerivation("DERIVED_AT_DECISION_TIME", EvidenceState.NOT_APPLICABLE, "QualityProfilePolicy", "quality-profile-policy-v0.1", "EXACT", "profile policy remains authoritative"),
        }

