# v0.1.0-preview.1 — Scientific Figure Reconstruction Skill

First proposed GitHub Research Preview for `fun4fang/scientific-figure-reconstruction-skill`.
Maintainer: Fang Zhe — [@fun4fang](https://github.com/fun4fang).
Project-authored code/docs: MIT, Copyright (c) 2026 Fang Zhe. AI demonstration
assets use CC BY 4.0 within owner-authorized rights, with Fang Zhe (@fun4fang)
attribution, AI-generated origin and illustrative context; see the [asset license](examples/showcase-ai/ASSET_PERMISSION.md).
This is a local candidate; no repository, commit, tag or release has been published.

## What this preview provides

Codex Host-Managed Skill is the default: use the signed-in host account, without
a separate OpenAI API key. The locked local Python entry prepares PNG/JPEG,
validates inferred Proposals and writes editable PPTX/SVG. Actual rendering and
Office editing require a separate trusted Windows desktop session. Optional
independent API mode remains available with its own account/consent requirements.

Support starts with structured workflows and simple scientific schematics.
Native text/containers and tested PowerPoint connectors are editable. SVG
connectors are static paths. Complex regions may remain raster or be rejected;
image interiors, measurements and chart data are not automatically native.

## Three examples and their actual scope

1. **Main AI-image Hero:** genuine built-in imagegen original, actual diagnostic
   PPTX/SVG renders and same-source Office export GIF. This is experimental
   PARTIAL reconstruction from a real image-only Proposal plus a local layering
   repair. The current public Skill cannot automatically reproduce that result.
   The local diagnostic adapter is not included. Icons/annotations remain
   independent assets; original fonts/arrows/layout differences remain visible.
2. **Program-generated UAV example:** secondary mixed native/raster reconstruction,
   with original scoped partial result and bounded Office edit records preserved.
3. **Basic Smoke Test:** Acquire → Validate → Report, accepted within its limited
   Windows example scope. Offline fixture runs use a known Proposal, not model vision.

## Changes from the frozen 183-file baseline

English/Chinese README, actual AI Hero and editing sequence, secondary UAV
Examples page, source/rights notes, concise release notes and complete new file
inventory. A minimal .gitattributes byte-preservation override prevents Git
line-ending conversion from invalidating the reviewed file hashes. Core source, Writers, validators, Skill, dependency pins and license
texts are preserved; NOTICE adds the separate AI asset license. The enhanced
payload has 214 files: 31 added, 7 changed, 0 removed and 176 baseline-identical.
No new model call, scientific algorithm, complex figure
Benchmark or full history audit. Original baseline tree SHA-256:
`cfaba8b149bb5bcf1d0dfa395b9c46f72a2de220e57b458c397bb5f4f0968424`.

GitHub tag recommendation is `v0.1.0-preview.1`; the inherited local Python
distribution metadata remains `0.1.0.dev0`. No PyPI release is proposed.
Current candidate installation/package checks are summarized in
[candidate verification](docs/release-verification.md), with historical editing
scope in [showcase](docs/showcase.md) and [basic verification](docs/verification.md).

## Limitations

AI Hero selection is presentation, not broader automatic product validation.
Unknown-image generalization, Linux/WSL, SVG GUI editors and full complex/3D
object reconstruction are unverified. Host context/tools/sandbox remain managed
by Codex; this Skill does not guarantee zero extra reads. Native editing does
not automatically repair collisions. Historical PHASE_7_ENTRY_GATE =
FAIL_UNCHANGED remains separate from this narrow preview.

Owner review and explicit GitHub publication authorization are still required.
