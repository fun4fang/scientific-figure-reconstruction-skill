from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from unified_figure.model.enums import CostClass, RepresentationType
from unified_figure.model.figure import CanonicalFigure
from unified_figure.model.records import EvidenceRecord, RendererRealization


@dataclass(frozen=True, slots=True)
class BackendCapabilityProfile:
    backend_id: str
    supports_native_text: bool
    supports_simple_shape: bool
    supports_native_relation: bool
    supports_vector_asset: bool
    supports_exact_raster: bool
    supports_generated_asset: bool
    supports_runtime_attachment: bool
    supports_editable_chart: bool
    supports_alpha: bool
    optional_dependency: bool
    cost_class: CostClass

    def supported_representations(self) -> frozenset[RepresentationType]:
        supported: set[RepresentationType] = set()
        flags = (
            (self.supports_native_text, RepresentationType.NATIVE_TEXT),
            (self.supports_simple_shape, RepresentationType.NATIVE_SIMPLE_SHAPE),
            (self.supports_simple_shape, RepresentationType.COMPOSITE_NATIVE),
            (self.supports_native_relation, RepresentationType.NATIVE_RELATION),
            (self.supports_vector_asset, RepresentationType.VECTOR_ASSET),
            (self.supports_exact_raster, RepresentationType.EXACT_RASTER_ASSET),
            (self.supports_exact_raster, RepresentationType.RASTER_PRESERVE_COMPLEX),
            (self.supports_generated_asset, RepresentationType.GENERATED_COMPLEX_ASSET),
            (self.supports_editable_chart, RepresentationType.EDITABLE_CHART),
        )
        for enabled, representation in flags:
            if enabled:
                supported.add(representation)
        return frozenset(supported)

    def supports(self, representation: RepresentationType) -> bool:
        return representation in self.supported_representations()


@dataclass(frozen=True, slots=True)
class AdapterValidationResult:
    valid: bool
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterRealizationResult:
    realizations: tuple[RendererRealization, ...]
    artifact_refs: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    artifact_hashes: tuple[tuple[str, str], ...] = ()
    adapter_config_hash: str = ""
    runtime_info: dict[str, str] = field(default_factory=dict)


class RendererAdapter(ABC):
    @abstractmethod
    def capabilities(self) -> BackendCapabilityProfile:
        raise NotImplementedError

    @abstractmethod
    def validate_input(self, figure: CanonicalFigure) -> AdapterValidationResult:
        raise NotImplementedError

    @abstractmethod
    def realize(self, figure: CanonicalFigure) -> AdapterRealizationResult:
        raise NotImplementedError

    @abstractmethod
    def validate_output(self, result: AdapterRealizationResult) -> AdapterValidationResult:
        raise NotImplementedError

    @abstractmethod
    def collect_runtime_evidence(
        self, result: AdapterRealizationResult
    ) -> tuple[EvidenceRecord, ...]:
        raise NotImplementedError
