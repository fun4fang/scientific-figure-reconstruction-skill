from __future__ import annotations

from hashlib import sha256
import json

from unified_figure.model.enums import CostClass, RepresentationType

from .contracts import (
    BackendAvailability,
    BackendCapability,
    BackendInputType,
    BackendOutputType,
    DeterminismLevel,
    EditabilityLevel,
    FidelityPotential,
    LatencyClass,
    ScientificContentRisk,
)


REGISTRY_VERSION = "backend-capability-registry-v0.1"


class BackendCapabilityRegistry:
    def __init__(self, capabilities: tuple[BackendCapability, ...] = ()) -> None:
        self._capabilities: dict[str, BackendCapability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: BackendCapability) -> None:
        if capability.backend_id in self._capabilities:
            raise ValueError(f"duplicate backend id: {capability.backend_id}")
        self._capabilities[capability.backend_id] = capability

    def get(self, backend_id: str) -> BackendCapability:
        try:
            return self._capabilities[backend_id]
        except KeyError as exc:
            raise KeyError(f"unknown backend: {backend_id}") from exc

    def discover(
        self,
        representation: RepresentationType,
        *,
        available_only: bool = True,
    ) -> tuple[BackendCapability, ...]:
        values = [item for item in self._capabilities.values() if item.supports(representation)]
        if available_only:
            values = [item for item in values if item.availability is BackendAvailability.AVAILABLE]
        return tuple(sorted(values, key=lambda item: item.backend_id))

    def to_dict(self) -> dict[str, object]:
        return {
            "registry_version": REGISTRY_VERSION,
            "backends": [self._capabilities[key].to_dict() for key in sorted(self._capabilities)],
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + sha256(payload).hexdigest()


def default_backend_registry(
    *,
    image_generation_available: bool = False,
    photoshop_refinement_available: bool = False,
) -> BackendCapabilityRegistry:
    native_common = {
        "can_generate": False,
        "can_edit": True,
        "can_extract": False,
        "can_vectorize": False,
        "can_composite": True,
        "supports_alpha": True,
        "supports_reference_guidance": False,
        "supports_local_region_operation": True,
        "supports_batch_assets": True,
        "editability_level": EditabilityLevel.FULLY_NATIVE,
        "fidelity_potential": FidelityPotential.LIMITED_FOR_COMPLEX_CONTENT,
        "scientific_content_risk": ScientificContentRisk.TRANSFORMATIVE_BUT_CONSTRAINED,
        "latency_class": LatencyClass.LOCAL_LOW,
        "cost_class": CostClass.LOW,
        "requires_model_call": False,
        "requires_external_software": False,
        "determinism_level": DeterminismLevel.DETERMINISTIC,
        "fallback_supported": True,
    }
    return BackendCapabilityRegistry(
        (
            BackendCapability(
                backend_id="PPTX_NATIVE",
                backend_version="pptx-adapter-v0.2",
                availability=BackendAvailability.AVAILABLE,
                supported_representation_types=(
                    RepresentationType.NATIVE_TEXT,
                    RepresentationType.NATIVE_SIMPLE_SHAPE,
                    RepresentationType.NATIVE_RELATION,
                    RepresentationType.COMPOSITE_NATIVE,
                ),
                supported_input_types=(BackendInputType.CANONICAL_COMPONENT,),
                supported_output_types=(BackendOutputType.PPTX_OBJECT,),
                **native_common,
            ),
            BackendCapability(
                backend_id="SVG_NATIVE",
                backend_version="svg-adapter-v0.2",
                availability=BackendAvailability.AVAILABLE,
                supported_representation_types=(
                    RepresentationType.NATIVE_TEXT,
                    RepresentationType.NATIVE_SIMPLE_SHAPE,
                    RepresentationType.NATIVE_RELATION,
                    RepresentationType.COMPOSITE_NATIVE,
                    RepresentationType.VECTOR_ASSET,
                ),
                supported_input_types=(BackendInputType.CANONICAL_COMPONENT,),
                supported_output_types=(BackendOutputType.SVG_OBJECT,),
                **native_common,
            ),
            BackendCapability(
                backend_id="EXACT_SOURCE_PRESERVE",
                backend_version="phase6f-coherent-source-preservation-v0.2",
                availability=BackendAvailability.AVAILABLE,
                supported_representation_types=(
                    RepresentationType.EXACT_RASTER_ASSET,
                    RepresentationType.RASTER_PRESERVE_COMPLEX,
                    RepresentationType.VECTOR_ASSET,
                ),
                supported_input_types=(BackendInputType.SOURCE_CROP,),
                supported_output_types=(BackendOutputType.RASTER_ASSET, BackendOutputType.VECTOR_ASSET),
                can_generate=False,
                can_edit=False,
                can_extract=True,
                can_vectorize=False,
                can_composite=True,
                supports_alpha=True,
                supports_reference_guidance=True,
                supports_local_region_operation=True,
                supports_batch_assets=True,
                editability_level=EditabilityLevel.ASSET_LEVEL,
                fidelity_potential=FidelityPotential.SOURCE_EXACT,
                scientific_content_risk=ScientificContentRisk.SOURCE_AUTHORITATIVE,
                latency_class=LatencyClass.LOCAL_LOW,
                cost_class=CostClass.LOW,
                requires_model_call=False,
                requires_external_software=False,
                determinism_level=DeterminismLevel.DETERMINISTIC,
                fallback_supported=True,
            ),
            BackendCapability(
                backend_id="IMAGE_GENERATION",
                backend_version="built-in-imagegen-phase6d1",
                availability=(
                    BackendAvailability.AVAILABLE
                    if image_generation_available
                    else BackendAvailability.UNAVAILABLE
                ),
                supported_representation_types=(RepresentationType.GENERATED_COMPLEX_ASSET,),
                supported_input_types=(
                    BackendInputType.REFERENCE_IMAGE,
                    BackendInputType.SOURCE_CROP,
                    BackendInputType.SEMANTIC_CONSTRAINTS,
                ),
                supported_output_types=(BackendOutputType.RASTER_ASSET,),
                can_generate=True,
                can_edit=True,
                can_extract=False,
                can_vectorize=False,
                can_composite=False,
                supports_alpha=True,
                supports_reference_guidance=True,
                supports_local_region_operation=True,
                supports_batch_assets=False,
                editability_level=EditabilityLevel.ASSET_LEVEL,
                fidelity_potential=FidelityPotential.HIGH,
                scientific_content_risk=ScientificContentRisk.GENERATIVE_NON_AUTHORITATIVE,
                latency_class=LatencyClass.EXTERNAL_HIGH,
                cost_class=CostClass.HIGH,
                requires_model_call=True,
                requires_external_software=False,
                determinism_level=DeterminismLevel.PROVIDER_VARIABLE,
                fallback_supported=True,
            ),
            BackendCapability(
                backend_id="PHOTOSHOP_REFINEMENT",
                backend_version="photoshop-refinement-v0.1",
                availability=(
                    BackendAvailability.AVAILABLE
                    if photoshop_refinement_available
                    else BackendAvailability.UNAVAILABLE
                ),
                supported_representation_types=(
                    RepresentationType.EXACT_RASTER_ASSET,
                    RepresentationType.RASTER_PRESERVE_COMPLEX,
                    RepresentationType.GENERATED_COMPLEX_ASSET,
                ),
                supported_input_types=(
                    BackendInputType.SOURCE_CROP,
                    BackendInputType.REFERENCE_IMAGE,
                ),
                supported_output_types=(BackendOutputType.RASTER_ASSET,),
                can_generate=False,
                can_edit=True,
                can_extract=True,
                can_vectorize=False,
                can_composite=True,
                supports_alpha=True,
                supports_reference_guidance=True,
                supports_local_region_operation=True,
                supports_batch_assets=False,
                editability_level=EditabilityLevel.ASSET_LEVEL,
                fidelity_potential=FidelityPotential.HIGH,
                scientific_content_risk=ScientificContentRisk.TRANSFORMATIVE_BUT_CONSTRAINED,
                latency_class=LatencyClass.EXTERNAL_MEDIUM,
                cost_class=CostClass.HIGH,
                requires_model_call=False,
                requires_external_software=True,
                determinism_level=DeterminismLevel.DETERMINISTIC,
                fallback_supported=True,
            ),
        )
    )
