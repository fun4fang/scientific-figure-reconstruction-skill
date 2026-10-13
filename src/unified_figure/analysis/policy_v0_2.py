from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from unified_figure.qa.coverage_v0_2 import coverage_reconciliation_policy_hash_v0_2

from .cache_v0_2 import CACHE_KEY_POLICY_VERSION_V0_2, cache_policy_hash_v0_2
from .cost_policy_v0_2 import (
    ANALYSIS_COST_POLICY_VERSION_V0_2,
    DEFAULT_ANALYSIS_COST_BUDGET_V0_2,
)
from .geometry_policy_v0_2 import DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2
from .policy import DEFAULT_ANALYSIS_EXECUTION_CONTRACT
from .roi_v0_2 import (
    ROI_PROMPT_HASH_V0_2,
    ROI_PROMPT_VERSION_V0_2,
    ROI_PROPOSAL_VERSION_V0_2,
    roi_proposal_schema_hash_v0_2,
)
from .validator_v0_2 import (
    DEPENDENCY_LOCALIZATION_POLICY_VERSION_V0_2,
    PROPOSAL_QUARANTINE_POLICY_VERSION_V0_2,
    PROPOSAL_VALIDATION_POLICY_VERSION_V0_2,
    validation_policy_hash_v0_2,
)


EXECUTION_CONTRACT_VERSION_V0_2 = "UNIFIED_ANALYSIS_EXECUTION_CONTRACT_V0_2"


@dataclass(frozen=True, slots=True)
class AnalysisExecutionContractV0_2:
    contract_version: str = EXECUTION_CONTRACT_VERSION_V0_2
    provider_name: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.provider_name
    model: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.model
    reasoning_effort: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.reasoning_effort
    full_image_prompt_version: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.prompt_version
    full_image_prompt_hash: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.prompt_hash
    full_image_proposal_schema_version: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.proposal_schema_version
    full_image_proposal_schema_hash: str = DEFAULT_ANALYSIS_EXECUTION_CONTRACT.proposal_schema_hash
    validation_policy_version: str = PROPOSAL_VALIDATION_POLICY_VERSION_V0_2
    validation_policy_hash: str = ""
    quarantine_policy_version: str = PROPOSAL_QUARANTINE_POLICY_VERSION_V0_2
    dependency_policy_version: str = DEPENDENCY_LOCALIZATION_POLICY_VERSION_V0_2
    geometry_policy_version: str = DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2.policy_version
    geometry_policy_hash: str = ""
    coverage_policy_version: str = "COVERAGE_RECONCILIATION_V0_2"
    coverage_policy_hash: str = ""
    roi_prompt_version: str = ROI_PROMPT_VERSION_V0_2
    roi_prompt_hash: str = ROI_PROMPT_HASH_V0_2
    roi_proposal_schema_version: str = ROI_PROPOSAL_VERSION_V0_2
    roi_proposal_schema_hash: str = ""
    roi_policy: str = "crop-local;question-local;schema-minimal;revision-local"
    full_image_call_limit: int = DEFAULT_ANALYSIS_COST_BUDGET_V0_2.full_image_call_limit
    roi_call_limit: int = DEFAULT_ANALYSIS_COST_BUDGET_V0_2.roi_call_limit
    total_analysis_call_limit: int = DEFAULT_ANALYSIS_COST_BUDGET_V0_2.total_analysis_call_limit
    quality_retries: int = 0
    infrastructure_retry_limit: int = 1
    cost_policy_version: str = ANALYSIS_COST_POLICY_VERSION_V0_2
    cache_policy_version: str = CACHE_KEY_POLICY_VERSION_V0_2
    cache_policy_hash: str = ""
    runtime_metadata_policy: str = "observable-only;NOT_EXPOSED-never-estimated"

    def __post_init__(self) -> None:
        defaults = {
            "validation_policy_hash": validation_policy_hash_v0_2(),
            "geometry_policy_hash": DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2.policy_hash(),
            "coverage_policy_hash": coverage_reconciliation_policy_hash_v0_2(),
            "roi_proposal_schema_hash": roi_proposal_schema_hash_v0_2(),
            "cache_policy_hash": cache_policy_hash_v0_2(),
        }
        for field, value in defaults.items():
            if not getattr(self, field):
                object.__setattr__(self, field, value)
        if self.full_image_call_limit != 1:
            raise ValueError("v0.2 permits one full-image call")
        if self.roi_call_limit < 0 or self.total_analysis_call_limit < 1:
            raise ValueError("invalid v0.2 call budget")
        if self.quality_retries != 0:
            raise ValueError("quality retries remain forbidden")
        if self.infrastructure_retry_limit > 1:
            raise ValueError("infrastructure retry limit must be <= 1")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def contract_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_ANALYSIS_EXECUTION_CONTRACT_V0_2 = AnalysisExecutionContractV0_2()
