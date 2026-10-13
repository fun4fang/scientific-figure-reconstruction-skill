from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Mapping, Sequence


PROVIDER_RUNTIME_RESOLUTION_POLICY_VERSION = "provider-runtime-resolution-policy-v0.2"


def _sha256_file(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def _version_key(identity: str) -> tuple[int, ...]:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9]+))?", identity)
    if not match:
        return ()
    return tuple(int(value or 0) for value in match.groups())


@dataclass(frozen=True, slots=True)
class ProviderRuntimeBinding:
    logical_provider_id: str
    resolved_executable: str
    provider_version: str
    executable_sha256: str
    resolution_method: str
    resolution_policy_version: str = PROVIDER_RUNTIME_RESOLUTION_POLICY_VERSION

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ProviderRuntimeResolver:
    """Resolve a logical Codex provider by a deterministic, auditable rule.

    A resolved path is evidence for one frozen execution contract.  It is not
    itself the provider's semantic identity and is never silently substituted
    after a contract has been frozen.
    """

    def __init__(
        self,
        *,
        logical_provider_id: str = "CODEX_RUNTIME",
        executable_name: str = "codex.exe" if os.name == "nt" else "codex",
    ) -> None:
        self.logical_provider_id = logical_provider_id
        self.executable_name = executable_name

    def resolve(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        explicit_executable: str | Path | None = None,
        managed_roots: Sequence[str | Path] | None = None,
    ) -> ProviderRuntimeBinding:
        env = dict(os.environ if environment is None else environment)
        candidates: list[tuple[str, Path]] = []
        if explicit_executable is not None:
            candidates.append(("EXPLICIT_CONTRACT_OVERRIDE", Path(explicit_executable)))
        else:
            on_path = shutil.which("codex", path=env.get("PATH"))
            if on_path:
                candidates.append(("CONTROLLED_PATH_LOOKUP", Path(on_path)))
            for root in self._managed_roots(env, managed_roots):
                if root.is_dir():
                    candidates.extend(
                        ("CODEX_APP_MANAGED_ROOT", path)
                        for path in sorted(root.glob(f"*/{self.executable_name}"))
                    )

        verified: list[tuple[tuple[int, ...], str, str, Path, str]] = []
        seen: set[str] = set()
        failures: list[str] = []
        for method, candidate in candidates:
            try:
                path = candidate.resolve(strict=True)
            except OSError as exc:
                failures.append(f"{method}:{candidate}:{type(exc).__name__}")
                continue
            normalized = os.path.normcase(str(path))
            if normalized in seen or not path.is_file():
                continue
            seen.add(normalized)
            probe = subprocess.run(
                [str(path), "--version"],
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=15,
            )
            identity = (probe.stdout or probe.stderr).strip()
            if probe.returncode or not identity:
                failures.append(f"{method}:{path}:probe_exit={probe.returncode}")
                continue
            verified.append((_version_key(identity), normalized, method, path, identity))

        if not verified:
            detail = "; ".join(failures) if failures else "no candidates"
            raise RuntimeError(
                f"logical provider {self.logical_provider_id} is unavailable under "
                f"{PROVIDER_RUNTIME_RESOLUTION_POLICY_VERSION}: {detail}"
            )

        # A controlled PATH binding is authoritative when present. Otherwise
        # select the highest verified managed runtime, then lexical path.
        path_bound = [item for item in verified if item[2] == "CONTROLLED_PATH_LOOKUP"]
        selected = path_bound[0] if path_bound else sorted(verified, key=lambda item: (item[0], item[1]))[-1]
        _, _, method, path, identity = selected
        return ProviderRuntimeBinding(
            logical_provider_id=self.logical_provider_id,
            resolved_executable=str(path),
            provider_version=identity,
            executable_sha256=_sha256_file(path),
            resolution_method=method,
        )

    @staticmethod
    def _managed_roots(
        environment: Mapping[str, str],
        managed_roots: Sequence[str | Path] | None,
    ) -> tuple[Path, ...]:
        if managed_roots is not None:
            return tuple(Path(item) for item in managed_roots)
        local_app_data = environment.get("LOCALAPPDATA")
        if not local_app_data:
            return ()
        return (Path(local_app_data) / "OpenAI" / "Codex" / "bin",)

