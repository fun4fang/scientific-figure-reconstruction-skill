from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path

from .roi_v0_3 import ROIAnalysisProposalV0_3


CACHE_KEY_POLICY_VERSION_V0_3 = "analysis-cache-key-policy-v0.3"


@dataclass(frozen=True, slots=True)
class ROIAnalysisCacheKeyV0_3:
    crop_hash: str
    prompt_hash: str
    schema_hash: str
    execution_contract_version: str
    execution_contract_hash: str
    provider_name: str
    runtime_identity: str
    question_type: str
    target_scope: tuple[str, ...]
    validation_policy_hash: str
    roi_policy_version: str
    compatibility_policy_hash: str
    cache_policy_version: str = CACHE_KEY_POLICY_VERSION_V0_3

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


class ROIAnalysisEvidenceCacheV0_3:
    """Exact v0.3 cache; prior contract and schema identities cannot match."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, key: ROIAnalysisCacheKeyV0_3) -> ROIAnalysisProposalV0_3 | None:
        entry = self.root / key.digest()
        key_path = entry / "cache_key.json"
        proposal_path = entry / "roi_proposal.json"
        if not key_path.exists() or not proposal_path.exists():
            return None
        if json.loads(key_path.read_text(encoding="utf-8")) != asdict(key):
            return None
        value = json.loads(proposal_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        return ROIAnalysisProposalV0_3.from_dict(value)

    def store(
        self,
        key: ROIAnalysisCacheKeyV0_3,
        proposal: ROIAnalysisProposalV0_3,
    ) -> Path:
        proposal.validate()
        entry = self.root / key.digest()
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "cache_key.json").write_text(
            json.dumps(asdict(key), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (entry / "roi_proposal.json").write_text(
            json.dumps(proposal.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return entry


def cache_policy_hash_v0_3() -> str:
    payload = json.dumps(
        {
            "version": CACHE_KEY_POLICY_VERSION_V0_3,
            "required_identity": (
                "crop_hash",
                "prompt_hash",
                "schema_hash",
                "execution_contract_version",
                "execution_contract_hash",
                "provider_name",
                "runtime_identity",
                "question_type",
                "target_scope",
                "validation_policy_hash",
                "roi_policy_version",
                "compatibility_policy_hash",
            ),
            "prior_version_compatibility": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()

