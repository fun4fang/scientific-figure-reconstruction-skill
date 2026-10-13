from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from time import perf_counter_ns
from typing import Any

from unified_figure.model.enums import AnalysisMode
from unified_figure.model.geometry import AuthoritativeInputGeometry, ModelValidationError

from .cache import AnalysisCacheKey, AnalysisEvidenceCache
from .contracts import AnalysisPolicy, AuthoritativeSourceReference, FigureAnalysisProvider
from .policy import AnalysisExecutionContract, DEFAULT_ANALYSIS_EXECUTION_CONTRACT
from .prompt import build_analysis_prompt
from .proposal import AnalysisProposal, AnalysisRuntimeMetadata
from .schema_compatibility import StructuredOutputCompatibilityValidator


@dataclass(frozen=True, slots=True)
class AnalysisRunReceipt:
    receipt_version: str
    cache_key: str
    cache_hit: bool
    input_image_hash: str
    attached_image_hash: str
    decoded_width_px: int
    decoded_height_px: int
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    proposal_schema_version: str
    proposal_schema_hash: str
    execution_contract_hash: str
    analysis_mode: str
    roi_descriptor: str
    infrastructure_attempts: int
    quality_retries: int
    input_tokens: int | str
    output_tokens: int | str
    wall_time_ms: float
    raw_response_hash: str
    parsed_proposal_hash: str
    trace_hash: str
    runtime_response_id: str
    runtime_version: str
    artifact_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AnalysisProviderRuntimeError(RuntimeError):
    pass


class MultimodalFigureAnalysisProvider(FigureAnalysisProvider):
    """Provider-isolated real multimodal analysis through a noninteractive Codex CLI."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        artifact_root: str | Path,
        cache_root: str | Path,
        proposal_schema_path: str | Path,
        contract: AnalysisExecutionContract = DEFAULT_ANALYSIS_EXECUTION_CONTRACT,
        codex_executable: str = "codex",
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        from unified_figure.file_boundary import checked_target
        self.artifact_root = checked_target(repository_root, artifact_root)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.cache = AnalysisEvidenceCache(cache_root)
        self.proposal_schema_path = Path(proposal_schema_path).resolve()
        self.contract = contract
        self.codex_executable = codex_executable
        self.last_receipt: AnalysisRunReceipt | None = None

    def analyze(
        self,
        source: AuthoritativeSourceReference,
        input_geometry: AuthoritativeInputGeometry,
        policy: AnalysisPolicy,
    ) -> AnalysisProposal:
        from unified_figure.security import BoundaryError
        raise BoundaryError('Legacy Codex Agent transport disabled in public candidate; use bounded author_image')
        source.validate()
        input_geometry.validate()
        policy.validate_for_execution()
        schema_preflight = StructuredOutputCompatibilityValidator().validate_file(
            self.proposal_schema_path,
            expected_schema_hash=self.contract.proposal_schema_hash,
        )
        self._write_json("provider_schema_preflight.json", schema_preflight.to_dict())
        schema_preflight.require_request_safe()
        if policy.max_full_image_calls > self.contract.max_full_image_calls:
            raise ModelValidationError("analysis policy exceeds full-image call contract")
        if policy.max_roi_calls > self.contract.max_roi_escalations:
            raise ModelValidationError("analysis policy exceeds ROI call contract")
        source_path = (self.repository_root / source.repository_relative_ref).resolve()
        if self.repository_root not in source_path.parents:
            raise ModelValidationError("source image escapes repository root")
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_bytes = source_path.read_bytes()
        source_hash = "sha256:" + sha256(source_bytes).hexdigest()
        if source_hash != source.source_hash or source_hash != input_geometry.source_hash:
            raise ModelValidationError("source bytes do not match authoritative source hash")
        decoded_width, decoded_height = self._decoded_size(source_path)
        if (decoded_width, decoded_height) != (
            input_geometry.decoded_width_px,
            input_geometry.decoded_height_px,
        ):
            raise ModelValidationError("deterministic decode disagrees with authoritative geometry")

        attached_path = source_path
        roi_descriptor = "FULL_IMAGE"
        roi_context = ""
        if policy.mode is AnalysisMode.ROI:
            assert policy.roi is not None
            roi_descriptor = (
                f"x={policy.roi.x:g},y={policy.roi.y:g},"
                f"w={policy.roi.width:g},h={policy.roi.height:g};"
                f"uncertainty={policy.linked_uncertainty_id or 'NONE'};"
                f"review={policy.linked_review_id or 'NONE'};"
                f"targets={','.join(policy.target_proposal_ids)}"
            )
            attached_path = self._prepare_roi(source_path, policy)
            roi_context = roi_descriptor + "; coordinates in output remain SOURCE_PIXELS of the original image"
        attached_hash = "sha256:" + sha256(attached_path.read_bytes()).hexdigest()
        cache_key = AnalysisCacheKey(
            input_image_hash=source_hash,
            analysis_policy_version=self.contract.contract_version,
            prompt_hash=self.contract.prompt_hash,
            provider_name=self.contract.provider_name,
            runtime_identity=f"codex-cli/{self.contract.model}/{self.contract.reasoning_effort}",
            proposal_schema_hash=self.contract.proposal_schema_hash,
            analysis_mode=policy.mode.value,
            roi_descriptor=roi_descriptor,
        )
        cached = self.cache.load(cache_key)
        if cached is not None:
            self.last_receipt = self._cache_hit_receipt(
                cached,
                cache_key,
                source_hash,
                attached_hash,
                decoded_width,
                decoded_height,
                policy,
                roi_descriptor,
            )
            self._write_json("analysis_receipt.json", self.last_receipt.to_dict())
            self._write_json("parsed_proposal.json", cached.to_dict())
            return cached

        prompt = build_analysis_prompt(
            source_ref=source.repository_relative_ref,
            mode=policy.mode.value,
            roi_context=roi_context,
        )
        raw_path = self.artifact_root / "raw_response.json"
        trace_path = self.artifact_root / "runtime_trace.jsonl"
        runtime_dir = self.artifact_root / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self.codex_executable,
            "exec",
            "--image",
            str(attached_path),
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
        while attempts <= self.contract.max_infrastructure_retries:
            attempts += 1
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            trace_path.write_text(completed.stdout, encoding="utf-8")
            if completed.returncode == 0 and raw_path.is_file():
                break
            if attempts > self.contract.max_infrastructure_retries or not self._transient_failure(
                completed.stderr
            ):
                raise AnalysisProviderRuntimeError(
                    f"multimodal provider failed (exit={completed.returncode}): "
                    + completed.stderr[-2000:]
                )
        assert completed is not None
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        raw_text = raw_path.read_text(encoding="utf-8")
        try:
            raw_data = json.loads(raw_text)
            if not isinstance(raw_data, dict):
                raise ValueError("response root is not an object")
            proposal = AnalysisProposal.from_dict(raw_data)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise AnalysisProviderRuntimeError(
                f"provider output failed AnalysisProposal decoding without quality retry: {exc}"
            ) from exc

        usage, response_id = self._parse_runtime_trace(completed.stdout)
        runtime = AnalysisRuntimeMetadata(
            provider=self.contract.provider_name,
            model=self.contract.model,
            response_id=response_id,
            prompt_version=self.contract.prompt_version,
            prompt_hash=self.contract.prompt_hash,
            proposal_schema_version=self.contract.proposal_schema_version,
            proposal_schema_hash=self.contract.proposal_schema_hash,
            input_hash=source_hash,
            analysis_mode=policy.mode.value,
            input_tokens=usage.get("input_tokens", "NOT_EXPOSED"),
            output_tokens=usage.get("output_tokens", "NOT_EXPOSED"),
            wall_time_ms=elapsed_ms,
            infrastructure_attempts=attempts,
            cache_hit=False,
        )
        proposal = replace(
            proposal,
            proposal_version=self.contract.proposal_schema_version,
            source_ref=source.repository_relative_ref,
            runtime_metadata=runtime,
        )
        parsed_path = self.artifact_root / "parsed_proposal.json"
        parsed_path.write_text(
            json.dumps(proposal.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        receipt = AnalysisRunReceipt(
            receipt_version="phase5c-analysis-receipt-v0.1",
            cache_key=cache_key.digest(),
            cache_hit=False,
            input_image_hash=source_hash,
            attached_image_hash=attached_hash,
            decoded_width_px=decoded_width,
            decoded_height_px=decoded_height,
            provider=self.contract.provider_name,
            model=self.contract.model,
            prompt_version=self.contract.prompt_version,
            prompt_hash=self.contract.prompt_hash,
            proposal_schema_version=self.contract.proposal_schema_version,
            proposal_schema_hash=self.contract.proposal_schema_hash,
            execution_contract_hash=self.contract.contract_hash(),
            analysis_mode=policy.mode.value,
            roi_descriptor=roi_descriptor,
            infrastructure_attempts=attempts,
            quality_retries=0,
            input_tokens=runtime.input_tokens,
            output_tokens=runtime.output_tokens,
            wall_time_ms=elapsed_ms,
            raw_response_hash=self._file_hash(raw_path),
            parsed_proposal_hash=self._file_hash(parsed_path),
            trace_hash=self._file_hash(trace_path),
            runtime_response_id=response_id,
            runtime_version=self._runtime_version(),
            artifact_refs=(
                raw_path.name,
                parsed_path.name,
                trace_path.name,
            ),
        )
        self.last_receipt = receipt
        self._write_json("analysis_receipt.json", receipt.to_dict())
        self.cache.store(cache_key, proposal, analysis_receipt=receipt.to_dict())
        return proposal

    def _prepare_roi(self, source_path: Path, policy: AnalysisPolicy) -> Path:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - environment failure path
            raise AnalysisProviderRuntimeError("ROI preparation requires Pillow") from exc
        assert policy.roi is not None
        left = max(0, int(policy.roi.x))
        top = max(0, int(policy.roi.y))
        right = int(policy.roi.x + policy.roi.width)
        bottom = int(policy.roi.y + policy.roi.height)
        if right <= left or bottom <= top:
            raise ModelValidationError("ROI must have positive pixel area")
        output = self.artifact_root / "roi_input.png"
        with Image.open(source_path) as image:
            right = min(image.width, right)
            bottom = min(image.height, bottom)
            crop = image.crop((left, top, right, bottom))
            crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.NEAREST)
            from unified_figure.file_boundary import save_asset
            output = save_asset(crop, self.artifact_root, "roi-input", root=self.artifact_root.parent, optimize=False, compress_level=9)
        return output

    @staticmethod
    def _decoded_size(path: Path) -> tuple[int, int]:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - environment failure path
            raise AnalysisProviderRuntimeError("deterministic image decode requires Pillow") from exc
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.width, image.height

    @staticmethod
    def _parse_runtime_trace(text: str) -> tuple[dict[str, int], str]:
        usage: dict[str, int] = {}
        response_id = "NOT_EXPOSED"
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                response_id = str(event.get("thread_id", "NOT_EXPOSED"))
            event_usage = event.get("usage")
            if isinstance(event_usage, dict):
                for key in ("input_tokens", "output_tokens"):
                    value = event_usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[key] = value
        return usage, response_id

    def _cache_hit_receipt(
        self,
        proposal: AnalysisProposal,
        key: AnalysisCacheKey,
        source_hash: str,
        attached_hash: str,
        width: int,
        height: int,
        policy: AnalysisPolicy,
        roi_descriptor: str,
    ) -> AnalysisRunReceipt:
        return AnalysisRunReceipt(
            receipt_version="phase5c-analysis-receipt-v0.1",
            cache_key=key.digest(),
            cache_hit=True,
            input_image_hash=source_hash,
            attached_image_hash=attached_hash,
            decoded_width_px=width,
            decoded_height_px=height,
            provider=self.contract.provider_name,
            model=self.contract.model,
            prompt_version=self.contract.prompt_version,
            prompt_hash=self.contract.prompt_hash,
            proposal_schema_version=self.contract.proposal_schema_version,
            proposal_schema_hash=self.contract.proposal_schema_hash,
            execution_contract_hash=self.contract.contract_hash(),
            analysis_mode=policy.mode.value,
            roi_descriptor=roi_descriptor,
            infrastructure_attempts=0,
            quality_retries=0,
            input_tokens=0,
            output_tokens=0,
            wall_time_ms=0.0,
            raw_response_hash="REUSED_BY_CACHE_KEY",
            parsed_proposal_hash=proposal.content_hash(),
            trace_hash="REUSED_BY_CACHE_KEY",
            runtime_response_id=proposal.runtime_metadata.response_id,
            runtime_version=self._runtime_version(),
            artifact_refs=("parsed_proposal.json", "analysis_receipt.json"),
        )

    def _runtime_version(self) -> str:
        try:
            result = subprocess.run(
                [self.codex_executable, "--version"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            return result.stdout.strip() or "NOT_EXPOSED"
        except OSError:
            return "NOT_EXPOSED"

    @staticmethod
    def _transient_failure(stderr: str) -> bool:
        lowered = stderr.lower()
        return any(
            token in lowered
            for token in ("timeout", "temporarily unavailable", "connection reset", "network")
        )

    @staticmethod
    def _file_hash(path: Path) -> str:
        return "sha256:" + sha256(path.read_bytes()).hexdigest()

    def _write_json(self, name: str, value: dict[str, Any]) -> None:
        (self.artifact_root / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
