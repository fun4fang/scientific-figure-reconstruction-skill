from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from .prompt import ANALYSIS_PROMPT_HASH, ANALYSIS_PROMPT_VERSION
from .proposal import PROPOSAL_VERSION, proposal_schema_hash


EXECUTION_CONTRACT_VERSION = "UNIFIED_ANALYSIS_EXECUTION_CONTRACT_V0_1"


@dataclass(frozen=True, slots=True)
class AnalysisExecutionContract:
    contract_version: str = EXECUTION_CONTRACT_VERSION
    provider_name: str = "codex-cli-multimodal"
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = "low"
    prompt_version: str = ANALYSIS_PROMPT_VERSION
    prompt_hash: str = ANALYSIS_PROMPT_HASH
    proposal_schema_version: str = PROPOSAL_VERSION
    proposal_schema_hash: str = ""
    max_full_image_calls: int = 1
    max_roi_escalations: int = 2
    max_infrastructure_retries: int = 1
    quality_retries: int = 0
    image_input_policy: str = "single-local-raster;decoded-dimensions-authoritative"
    roi_policy: str = "typed-uncertainty-or-review-bound;crop-and-upscale;no-unrelated-rewrite"
    cache_policy: str = "exact-sha256-contract-identity-only"
    cost_policy: str = "observable-values-only;unexposed-values-marked-NOT_EXPOSED"
    runtime_metadata_policy: str = "record-jsonl-events;no-fabricated-runtime-fields"

    def __post_init__(self) -> None:
        if not self.proposal_schema_hash:
            object.__setattr__(self, "proposal_schema_hash", proposal_schema_hash())
        if self.max_full_image_calls != 1:
            raise ValueError("Phase 5C contract requires exactly one full-image call budget")
        if not 0 <= self.max_roi_escalations <= 2:
            raise ValueError("Phase 5C ROI budget must be within [0, 2]")
        if self.max_infrastructure_retries > 1:
            raise ValueError("infrastructure retry budget must be <= 1")
        if self.quality_retries != 0:
            raise ValueError("quality retry is forbidden")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def contract_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_ANALYSIS_EXECUTION_CONTRACT = AnalysisExecutionContract()

CACHE_KEY_POLICY_VERSION = "analysis-cache-key-policy-v0.1"
ROI_POLICY_VERSION = "analysis-roi-policy-v0.1"
CACHE_KEY_POLICY_HASH = "sha256:" + sha256(
    DEFAULT_ANALYSIS_EXECUTION_CONTRACT.cache_policy.encode("utf-8")
).hexdigest()
ROI_POLICY_HASH = "sha256:" + sha256(
    DEFAULT_ANALYSIS_EXECUTION_CONTRACT.roi_policy.encode("utf-8")
).hexdigest()
