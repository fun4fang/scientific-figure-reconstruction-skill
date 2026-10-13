from .contracts import (
    AnalysisPolicy,
    AuthoritativeSourceReference,
    CanonicalAnalysisProposal,
    FigureAnalysisProvider,
    ProposedInputGeometry,
    preserve_authoritative_geometry,
)
from .cache import AnalysisCacheKey, AnalysisEvidenceCache
from .canonicalizer import (
    CANONICALIZATION_POLICY_VERSION,
    CanonicalizationPolicy,
    CanonicalizationReceipt,
    CanonicalizationResult,
    ProposalCanonicalizer,
)
from .policy import (
    CACHE_KEY_POLICY_HASH,
    CACHE_KEY_POLICY_VERSION,
    DEFAULT_ANALYSIS_EXECUTION_CONTRACT,
    EXECUTION_CONTRACT_VERSION,
    ROI_POLICY_HASH,
    ROI_POLICY_VERSION,
    AnalysisExecutionContract,
)
from .prompt import (
    ANALYSIS_PROMPT_HASH,
    ANALYSIS_PROMPT_VERSION,
    UNIFIED_FIGURE_ANALYSIS_PROMPT_V0_1,
    build_analysis_prompt,
)
from .proposal import *
from .provider import (
    AnalysisProviderRuntimeError,
    AnalysisRunReceipt,
    MultimodalFigureAnalysisProvider,
)
from .runtime import (
    PROVIDER_RUNTIME_RESOLUTION_POLICY_VERSION,
    ProviderRuntimeBinding,
    ProviderRuntimeResolver,
)
from .metrics import evaluate_structural_metrics
from .orchestration import (
    FidelityReferenceRealizationResult,
    ReferenceRealizationResult,
    analyze_reference_image,
    decode_reference_image,
    realize_reference_figure,
    realize_reference_figure_fidelity,
)
from .validator import (
    AnalysisProposalValidator,
    ProposalValidationIssue,
    ProposalValidationResult,
)
from .cache_v0_2 import *
from .canonicalizer_v0_2 import *
from .cost_policy_v0_2 import *
from .geometry_policy_v0_2 import *
from .policy_v0_2 import *
from .roi_v0_2 import *
from .cache_v0_3 import *
from .policy_v0_3 import *
from .roi_provider_v0_3 import *
from .roi_v0_3 import *
from .schema_compatibility import *
from .validator_v0_2 import (
    AnalysisProposalValidatorV0_2,
    ProposalDependencyImpact,
    ProposalIdRemap,
    ProposalItemDisposition,
    ProposalItemRef,
    ProposalReviewCandidateV0_2,
    ProposalValidationEvidence,
    ProposalValidationResult as ProposalValidationResultV0_2,
    validation_policy_hash_v0_2,
)

__all__ = [
    "AnalysisPolicy",
    "AuthoritativeSourceReference",
    "CanonicalAnalysisProposal",
    "FigureAnalysisProvider",
    "ProposedInputGeometry",
    "preserve_authoritative_geometry",
    "AnalysisCacheKey",
    "AnalysisEvidenceCache",
    "CANONICALIZATION_POLICY_VERSION",
    "CanonicalizationPolicy",
    "CanonicalizationReceipt",
    "CanonicalizationResult",
    "ProposalCanonicalizer",
    "DEFAULT_ANALYSIS_EXECUTION_CONTRACT",
    "EXECUTION_CONTRACT_VERSION",
    "CACHE_KEY_POLICY_HASH",
    "CACHE_KEY_POLICY_VERSION",
    "ROI_POLICY_HASH",
    "ROI_POLICY_VERSION",
    "AnalysisExecutionContract",
    "ANALYSIS_PROMPT_HASH",
    "ANALYSIS_PROMPT_VERSION",
    "UNIFIED_FIGURE_ANALYSIS_PROMPT_V0_1",
    "build_analysis_prompt",
    "AnalysisProviderRuntimeError",
    "AnalysisRunReceipt",
    "MultimodalFigureAnalysisProvider",
    "AnalysisProposalValidator",
    "ProposalValidationIssue",
    "ProposalValidationResult",
    "evaluate_structural_metrics",
    "ReferenceRealizationResult",
    "analyze_reference_image",
    "decode_reference_image",
    "realize_reference_figure",
]

# Proposal dataclasses are part of the public analysis contract as well.
__all__ = [name for name in globals() if not name.startswith("_")]
