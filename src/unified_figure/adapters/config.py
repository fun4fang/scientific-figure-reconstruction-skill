from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json


@dataclass(frozen=True, slots=True)
class RendererConfig:
    """Stable, backend-neutral policy identity for one adapter invocation."""

    adapter_version: str
    capability_profile_version: str
    render_policy_version: str
    font_policy: str
    asset_policy: str
    relation_policy: str

    def canonical_json(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def config_hash(self) -> str:
        return "sha256:" + sha256(self.canonical_json().encode("utf-8")).hexdigest()
