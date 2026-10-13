from __future__ import annotations

from hashlib import sha256

from unified_figure.model.enums import (
    CostClass,
    EvidenceSourceType,
    RealizationStatus,
    RuntimeVerification,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.records import EvidenceRecord, RendererRealization

from .contracts import (
    AdapterRealizationResult,
    AdapterValidationResult,
    BackendCapabilityProfile,
    RendererAdapter,
)


class SyntheticRendererAdapter(RendererAdapter):
    """No-I/O adapter for interface and identity regression tests."""

    def capabilities(self) -> BackendCapabilityProfile:
        return BackendCapabilityProfile(
            backend_id="synthetic",
            supports_native_text=True,
            supports_simple_shape=True,
            supports_native_relation=True,
            supports_vector_asset=True,
            supports_exact_raster=True,
            supports_generated_asset=False,
            supports_runtime_attachment=False,
            supports_editable_chart=False,
            supports_alpha=True,
            optional_dependency=False,
            cost_class=CostClass.LOW,
        )

    def validate_input(self, figure: CanonicalFigure) -> AdapterValidationResult:
        try:
            figure.validate()
        except ValueError as exc:
            return AdapterValidationResult(False, (str(exc),))
        supported = self.capabilities().supported_representations()
        unsupported = sorted(
            component.component_id
            for component in figure.components
            if component.representation not in supported
        )
        if unsupported:
            return AdapterValidationResult(False, tuple(f"unsupported: {item}" for item in unsupported))
        return AdapterValidationResult(True)

    def realize(self, figure: CanonicalFigure) -> AdapterRealizationResult:
        validation = self.validate_input(figure)
        if not validation.valid:
            raise ValueError("synthetic adapter rejected input: " + "; ".join(validation.messages))
        realizations = tuple(
            RendererRealization(
                realization_id=make_stable_id(
                    figure.figure_id, "synthetic-realization", index, component.component_id
                ),
                canonical_object_id=component.component_id,
                contributing_canonical_object_ids=(),
                backend="synthetic",
                backend_object_id=f"synthetic-object-{index:04d}",
                representation_used=component.representation,
                artifact_ref=None,
                status=RealizationStatus.VALIDATED,
                runtime_verification=RuntimeVerification.NOT_APPLICABLE,
                warnings=(),
            )
            for index, component in enumerate(figure.components)
        )
        return AdapterRealizationResult(realizations=realizations, artifact_refs=())

    def validate_output(self, result: AdapterRealizationResult) -> AdapterValidationResult:
        ids = [item.realization_id for item in result.realizations]
        return AdapterValidationResult(
            valid=len(ids) == len(set(ids)),
            messages=() if len(ids) == len(set(ids)) else ("duplicate realization IDs",),
        )

    def collect_runtime_evidence(
        self, result: AdapterRealizationResult
    ) -> tuple[EvidenceRecord, ...]:
        digest = sha256(
            "\n".join(item.realization_id for item in result.realizations).encode("utf-8")
        ).hexdigest()
        return (
            EvidenceRecord(
                evidence_id="evidence-synthetic-runtime-0000",
                source_type=EvidenceSourceType.RENDERER,
                source_hash=digest,
                producer="SyntheticRendererAdapter",
                operation="collect_runtime_evidence",
                input_refs=tuple(item.realization_id for item in result.realizations),
                output_refs=(),
                run_index=0,
                tool_runtime_info={"backend": "synthetic", "external_calls": "0"},
                notes="No external renderer was invoked.",
            ),
        )
