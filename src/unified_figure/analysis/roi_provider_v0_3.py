from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from time import perf_counter_ns

from .cache_v0_3 import ROIAnalysisCacheKeyV0_3, ROIAnalysisEvidenceCacheV0_3
from .policy_v0_3 import (
    AnalysisExecutionContractV0_3,
    DEFAULT_ANALYSIS_EXECUTION_CONTRACT_V0_3,
)
from .provider import MultimodalFigureAnalysisProvider
from .roi_v0_3 import LowContextROIRequestV0_3, ROIAnalysisProposalV0_3
from .schema_compatibility import StructuredOutputCompatibilityValidator


@dataclass(frozen=True, slots=True)
class ROIAnalysisRunReceiptV0_3:
    receipt_version: str
    cache_key: str
    cache_hit: bool
    crop_hash: str
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    proposal_schema_version: str
    proposal_schema_hash: str
    execution_contract_hash: str
    compatibility_policy_hash: str
    question_type: str
    target_scope: tuple[str, ...]
    infrastructure_attempts: int
    quality_retries: int
    input_tokens: int | str
    output_tokens: int | str
    wall_time_ms: float
    response_id: str
    raw_response_hash: str
    parsed_proposal_hash: str
    trace_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MultimodalROIAnalysisProviderV0_3:
    """Question-local ROI provider with schema preflight before inference."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        cache_root: str | Path,
        proposal_schema_path: str | Path,
        contract: AnalysisExecutionContractV0_3 = DEFAULT_ANALYSIS_EXECUTION_CONTRACT_V0_3,
        codex_executable: str = "codex",
    ) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.cache = ROIAnalysisEvidenceCacheV0_3(cache_root)
        self.proposal_schema_path = Path(proposal_schema_path).resolve()
        self.contract = contract
        self.codex_executable = codex_executable
        self.last_receipt: ROIAnalysisRunReceiptV0_3 | None = None

    def analyze(
        self,
        crop_path: str | Path,
        request: LowContextROIRequestV0_3,
    ) -> ROIAnalysisProposalV0_3:
        from unified_figure.security import BoundaryError
        raise BoundaryError('Legacy Codex Agent transport disabled in public candidate; use bounded author_image')
        schema_preflight = StructuredOutputCompatibilityValidator().validate_file(
            self.proposal_schema_path,
            expected_schema_hash=self.contract.roi_proposal_schema_hash,
        )
        self._write_json("provider_schema_preflight.json", schema_preflight.to_dict())
        schema_preflight.require_request_safe()
        if request.output_schema_hash != self.contract.roi_proposal_schema_hash:
            raise ValueError("ROI request schema hash does not match execution contract v0.3")
        if request.prompt_hash != self.contract.roi_prompt_hash:
            raise ValueError("ROI request prompt hash does not match execution contract v0.3")

        crop = Path(crop_path).resolve()
        if not crop.is_file():
            raise FileNotFoundError(crop)
        crop_hash = self._file_hash(crop)
        if crop_hash != request.crop_hash:
            raise ValueError("ROI crop hash does not match request")

        runtime_identity = (
            f"codex-cli/{self.contract.model}/{self.contract.reasoning_effort}"
        )
        cache_key = ROIAnalysisCacheKeyV0_3(
            crop_hash=crop_hash,
            prompt_hash=request.prompt_hash,
            schema_hash=request.output_schema_hash,
            execution_contract_version=self.contract.contract_version,
            execution_contract_hash=self.contract.contract_hash(),
            provider_name=self.contract.provider_name,
            runtime_identity=runtime_identity,
            question_type=request.question_type,
            target_scope=(request.target_review_id, *request.target_canonical_refs),
            validation_policy_hash=self.contract.validation_policy_hash,
            roi_policy_version=self.contract.roi_policy,
            compatibility_policy_hash=self.contract.provider_compatibility_policy_hash,
        )
        cached = self.cache.load(cache_key)
        if cached is not None:
            receipt = ROIAnalysisRunReceiptV0_3(
                receipt_version="phase5fc-roi-analysis-receipt-v0.3",
                cache_key=cache_key.digest(),
                cache_hit=True,
                crop_hash=crop_hash,
                provider=self.contract.provider_name,
                model=self.contract.model,
                prompt_version=request.prompt_version,
                prompt_hash=request.prompt_hash,
                proposal_schema_version=self.contract.roi_proposal_schema_version,
                proposal_schema_hash=self.contract.roi_proposal_schema_hash,
                execution_contract_hash=self.contract.contract_hash(),
                compatibility_policy_hash=self.contract.provider_compatibility_policy_hash,
                question_type=request.question_type,
                target_scope=(request.target_review_id, *request.target_canonical_refs),
                infrastructure_attempts=0,
                quality_retries=0,
                input_tokens=0,
                output_tokens=0,
                wall_time_ms=0.0,
                response_id="REUSED_BY_CACHE_KEY",
                raw_response_hash="REUSED_BY_CACHE_KEY",
                parsed_proposal_hash=cached.content_hash(),
                trace_hash="REUSED_BY_CACHE_KEY",
            )
            self.last_receipt = receipt
            self._write_json("analysis_receipt.json", receipt.to_dict())
            self._write_json("parsed_proposal.json", cached.to_dict())
            return cached

        raw_path = self.artifact_root / "raw_response.json"
        trace_path = self.artifact_root / "runtime_trace.jsonl"
        runtime_dir = self.artifact_root / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self.codex_executable,
            "exec",
            "--image",
            str(crop),
            "--model",
            self.contract.model,
            "-c",
            f'model_reasoning_effort="{self.contract.reasoning_effort}"',
            "--sandbox",
            "read-only",
            "--cd",
            str(runtime_dir),
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--output-schema",
            str(self.proposal_schema_path),
            "--json",
            "--output-last-message",
            str(raw_path),
            "-",
        ]
        started = perf_counter_ns()
        attempts = 0
        completed: subprocess.CompletedProcess[str] | None = None
        while attempts <= self.contract.infrastructure_retry_limit:
            attempts += 1
            completed = subprocess.run(
                command,
                input=request.canonical_json(),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            trace_path.write_text(completed.stdout, encoding="utf-8")
            if completed.returncode == 0 and raw_path.is_file():
                break
            if (
                attempts > self.contract.infrastructure_retry_limit
                or not MultimodalFigureAnalysisProvider._transient_failure(completed.stderr)
            ):
                raise RuntimeError(
                    f"ROI v0.3 provider failed (exit={completed.returncode}): "
                    + completed.stderr[-2000:]
                )
        assert completed is not None
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("ROI provider response root is not an object")
        proposal = ROIAnalysisProposalV0_3.from_dict(raw)
        if proposal.target_review_id != request.target_review_id:
            raise ValueError("ROI provider changed target review scope")
        if proposal.target_canonical_refs != request.target_canonical_refs:
            raise ValueError("ROI provider changed canonical target scope")
        if proposal.question_type != request.question_type:
            raise ValueError("ROI provider changed question type")

        usage, response_id = MultimodalFigureAnalysisProvider._parse_runtime_trace(
            completed.stdout
        )
        self.cache.store(cache_key, proposal)
        self._write_json("parsed_proposal.json", proposal.to_dict())
        receipt = ROIAnalysisRunReceiptV0_3(
            receipt_version="phase5fc-roi-analysis-receipt-v0.3",
            cache_key=cache_key.digest(),
            cache_hit=False,
            crop_hash=crop_hash,
            provider=self.contract.provider_name,
            model=self.contract.model,
            prompt_version=request.prompt_version,
            prompt_hash=request.prompt_hash,
            proposal_schema_version=self.contract.roi_proposal_schema_version,
            proposal_schema_hash=self.contract.roi_proposal_schema_hash,
            execution_contract_hash=self.contract.contract_hash(),
            compatibility_policy_hash=self.contract.provider_compatibility_policy_hash,
            question_type=request.question_type,
            target_scope=(request.target_review_id, *request.target_canonical_refs),
            infrastructure_attempts=attempts,
            quality_retries=0,
            input_tokens=usage.get("input_tokens", "NOT_EXPOSED"),
            output_tokens=usage.get("output_tokens", "NOT_EXPOSED"),
            wall_time_ms=elapsed_ms,
            response_id=response_id,
            raw_response_hash=self._file_hash(raw_path),
            parsed_proposal_hash=proposal.content_hash(),
            trace_hash=self._file_hash(trace_path),
        )
        self.last_receipt = receipt
        self._write_json("analysis_receipt.json", receipt.to_dict())
        return proposal

    @staticmethod
    def _file_hash(path: str | Path) -> str:
        return "sha256:" + sha256(Path(path).read_bytes()).hexdigest()

    def _write_json(self, name: str, value: dict[str, object]) -> None:
        (self.artifact_root / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

