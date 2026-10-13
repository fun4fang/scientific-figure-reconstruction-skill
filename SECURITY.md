# Security scope and reporting

This Research Preview targets a trusted single-user Windows workspace, not an
OS sandbox or multi-tenant service. Hostile concurrent directory replacement
is outside the guarantee: ancestor checks/exclusive files are not mandatory
directory ACL isolation against another local process. Scientific inference
is untrusted and human review remains necessary.

## Default Codex Host-Managed boundary

Codex performs visual Authoring in its existing session/account. Host AGENTS.md,
history, global Skills, shell/file/MCP tools, approvals and sandbox are controlled
by the host. This Skill does not attest the host context, forbid all extra file
reads, remove tools or make prompt injection impossible. A valid Proposal may
contain wrong scientific interpretation; local schema/content checks cannot
detect every model error. Use explicit approved inputs in a dedicated workspace
without secrets and review host access/permissions. Never let pixels/Proposal
strings grant tools, trigger extra reads/uploads or become shell command text.
These are host workflow rules; they are not a technical zero-tool boundary.

`prepare` strips metadata and exports existing schema/instructions without model
calls; `realize` treats literal JSON as untrusted data and passes the existing
scientific checks before Writers. Both are offline workers with scoped Python
writes/resource limits. Host file tools that write the Proposal are outside the
worker guard: approved location/host permission policy must constrain them.
No nested Codex process, credential/token discovery or global host changes occur.
External host network/file/tools are not controlled by the worker socket hook.
The original image-only Skill acceptance and separate trusted desktop checks
passed in the recorded limited Windows scope. This is not an adversarial-host
or zero-extra-file-read attestation. Host sandbox permissions were not changed.

Default model-local work uses `serialize`, not desktop COM. The human starts
`desktop-render` separately in the ordinary Windows desktop. Its packet has
four fixed paths; manifest commands, additional paths and integrity mismatches
are rejected. Image/Canonical identity, dimensions and passive PPTX/SVG checks
are repeated before rendering. Output paths remain fresh and guarded. A missing
or elevated desktop token fails closed; no privilege fallback is implemented.
No listening service, model command dispatcher or automatic desktop launch is
provided. Native Office/Edge are trusted processes; OS/libraries/caches are not
claimed read-isolated, and a hostile concurrent file mutation is out of scope.

## Optional API boundary retained

The optional explicit-input OpenAI Responses Client has no model tools or tool
dispatcher and sends fixed schema/guidance and approved images only. No Codex
auth/context discovery; tools=[], tool_choice=none; tool responses rejected.
Its local capability boundary remains offline verified, live API compatibility
unverified. This stronger boundary is not claimed for the default Codex host.
See [configuration](docs/configuration.md) and [API details](docs/optional-api.md).

All source asset persistence sites use shared `file_boundary.save_asset`.
Semantic IDs/references stay intact; disk names use a full SHA-256 of an accepted
identity. Unsafe IDs fail closed rather than silently clean/merge/drop material.
Colons/NTFS streams, separators, traversal/absolute names, Windows reserved
devices, illegal/control characters, trailing dots/spaces and excessive length
are rejected. Exclusive creation refuses existing names and digest collisions.
Actual returned paths are recorded in AssetReference. Symlinks/junctions/reparse
points and existing hard-linked files are rejected before resolution. Limits:
180-character filename, 128-character ASCII Proposal ID, 240-character absolute
output path. Use a short trusted workspace.

Parent and worker share an output-scoped Python write audit guard for serializer,
cache/comparison and crop writes. All Python outputs stay in the fresh authorized
job directory, link creation is forbidden. Office/Edge are trusted local native
renderers with fixed prechecked paths; this hook is not an OS constraint on them.
Library consumers supply trusted canonical states/fresh scoped paths; the CLI
provides the full consent/job/resource boundary. Historical Agent analyze methods
are disabled before discovery, cache or execution, including ROI providers.

Image/proposal/resource budgets and Windows child memory/lifecycle limits remain.
An existing Office COM server is outside child memory limits. Office/Edge may
maintain desktop caches. Active/external SVG and PPTX macros/external relationships/
arbitrary objects are refused before native rendering. Reconstruction images are
single-frame PNG/JPEG only; offline realization blocks Python network connections.

For a sensitive vulnerability, contact [Fang Zhe](https://github.com/fun4fang)
to agree on a private channel. No private security mailbox/advisory endpoint is
configured. Do not post credentials/private figures/exploit data in public issues.
Include the affected version/interface and a synthetic minimal reproduction.
