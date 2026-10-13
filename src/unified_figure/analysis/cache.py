from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path

from .proposal import AnalysisProposal


@dataclass(frozen=True, slots=True)
class AnalysisCacheKey:
    input_image_hash: str
    analysis_policy_version: str
    prompt_hash: str
    provider_name: str
    runtime_identity: str
    proposal_schema_hash: str
    analysis_mode: str
    roi_descriptor: str

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


class AnalysisEvidenceCache:
    """Exact-identity cache; there is deliberately no approximate lookup."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, key: AnalysisCacheKey) -> AnalysisProposal | None:
        entry = self.root / key.digest()
        key_path = entry / "cache_key.json"
        proposal_path = entry / "parsed_proposal.json"
        if not key_path.exists() or not proposal_path.exists():
            return None
        stored = json.loads(key_path.read_text(encoding="utf-8"))
        if stored != asdict(key):
            return None
        return AnalysisProposal.from_json(proposal_path.read_text(encoding="utf-8"))

    def store(
        self,
        key: AnalysisCacheKey,
        proposal: AnalysisProposal,
        *,
        analysis_receipt: dict[str, object],
    ) -> Path:
        entry = self.root / key.digest()
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "cache_key.json").write_text(
            json.dumps(asdict(key), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (entry / "parsed_proposal.json").write_text(
            json.dumps(proposal.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (entry / "analysis_receipt.json").write_text(
            json.dumps(analysis_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return entry
