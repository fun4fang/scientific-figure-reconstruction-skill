"""Public API for the first-party unified scientific-figure core."""

from .adapters import (
    AdapterRealizationResult,
    AdapterValidationResult,
    BackendCapabilityProfile,
    PptxRendererAdapter,
    RendererAdapter,
    RendererConfig,
    SvgRendererAdapter,
    SyntheticRendererAdapter,
)
from .analysis import (
    AnalysisPolicy,
    AuthoritativeSourceReference,
    CanonicalAnalysisProposal,
    FigureAnalysisProvider,
    ProposedInputGeometry,
    preserve_authoritative_geometry,
)
from .analysis import *
from .backends import *
from .model import *
from .model.enums import *
from .qa import (
    CanonicalStructureValidator,
    CompletionEvaluation,
    CompletionEvaluator,
    CompletionStatus,
    CoverageValidator,
    CoverageReconciliationRecord,
    CoverageReconciliationResult,
    CoverageReconcilerV0_2,
    RepresentationValidator,
    SemanticParityResult,
    validate_cross_backend_parity,
)
from .routing import (
    CostPolicy,
    DeterministicSyntheticRouter,
    EditingRequirements,
    RepresentationRouter,
    RepresentationRoutingPolicyV0_1,
    SemanticContext,
    route_figure,
)
from .pipeline import (
    RealizationPipelineResult,
    RealizationRunReceipt,
    render_figure,
    validate_realization,
)
from .serialization import build_json_schema, canonical_json, canonical_loads, canonical_state_hash

__all__ = [name for name in globals() if not name.startswith("_")]
