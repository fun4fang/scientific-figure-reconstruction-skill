from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path

from .roi_v0_2 import ROIAnalysisProposalV0_2, ROILocalObservationV0_2


CACHE_KEY_POLICY_VERSION_V0_2 = "analysis-cache-key-policy-v0.2"


@dataclass(frozen=True, slots=True)
class ROIAnalysisCacheKeyV0_2:
    crop_hash: str
    prompt_hash: str
    schema_hash: str
    execution_contract_version: str
    provider_name: str
    runtime_identity: str
    question_type: str
    target_scope: tuple[str, ...]
    validation_policy_hash: str
    roi_policy_version: str
    cache_policy_version: str = CACHE_KEY_POLICY_VERSION_V0_2

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


class ROIAnalysisEvidenceCacheV0_2:
    """Exact v0.2 ROI cache with no compatibility path to v0.1 entries."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, key: ROIAnalysisCacheKeyV0_2) -> ROIAnalysisProposalV0_2 | None:
        entry = self.root / key.digest()
        key_path = entry / "cache_key.json"
        proposal_path = entry / "roi_proposal.json"
        if not key_path.exists() or not proposal_path.exists():
            return None
        if json.loads(key_path.read_text(encoding="utf-8")) != asdict(key):
            return None
        value = json.loads(proposal_path.read_text(encoding="utf-8"))
        proposal = ROIAnalysisProposalV0_2(
            roi_version=value["roi_version"],
            target_review_id=value["target_review_id"],
            target_canonical_refs=tuple(value["target_canonical_refs"]),
            question_type=value["question_type"],
            local_observations=tuple(
                ROILocalObservationV0_2(**item) for item in value["local_observations"]
            ),
            candidate_resolution=value["candidate_resolution"],
            confidence=value["confidence"],
            remaining_uncertainty=value["remaining_uncertainty"],
            local_evidence=tuple(value["local_evidence"]),
        )
        proposal.validate()
        return proposal

    def store(
        self,
        key: ROIAnalysisCacheKeyV0_2,
        proposal: ROIAnalysisProposalV0_2,
    ) -> Path:
        proposal.validate()
        entry = self.root / key.digest()
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "cache_key.json").write_text(
            json.dumps(asdict(key), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (entry / "roi_proposal.json").write_text(
            json.dumps(asdict(proposal), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return entry


def cache_policy_hash_v0_2() -> str:
    return "sha256:" + sha256(CACHE_KEY_POLICY_VERSION_V0_2.encode("utf-8")).hexdigest()
