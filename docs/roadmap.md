# Representation-aware research directions

Status checked against the selected candidate implementation and the existing development roadmap on 2026-10-12; no code changed and no experiment was rerun.

## What exists

The candidate defines FAST, BALANCED and HIGH in src/unified_figure/routing/quality_profiles.py. Its actual image-Authoring realization in src/unified_figure/reauthoring.py fixes get_quality_profile("BALANCED"). src/unified_figure/cli.py does not expose a --profile selector. Existing policy enumerations and budget fields do not establish three usable, independently validated modes, nor do their numeric budgets bound total Codex host Authoring turns.

| Direction | Intended research behavior | Current state |
|---|---|---|
| FAST | Short-latency/lower-cost scientific and visual preservation; native structure where reliable, retain complex source regions | Policy/routing foundation IMPLEMENTED. Full speed, cost, fidelity and editing claims PARTIAL / TO VERIFY. No published per-mode benchmark. |
| BALANCED | Future default: coherent editable text/containers/relations plus safely retained source; eventual useful independent object layers | Policy IMPLEMENTED and used by current Authoring realization. Regular editing and one mixed-image case are limited evidence; complete mode, reliable irregular semantic separation and general transparent layers PLANNED. |
| HIGH | More granular complex-figure editing: irregular instances, transparency, reliable contours, selective vectorization, hierarchy and internal subcomponents | Legacy policy foundation IMPLEMENTED / PARTIAL. Full advanced mode PLANNED and not end-to-end validated. |

Past mode comparisons did not establish a faster/better/cheaper winner. Existing failure conclusions remain; no efficacy claim is made here.

## Representation choices

- Native text/containers and regular shapes: implemented; accepted basic example and controlled editing evidence.
- Vector paths: existing writers serialize supported geometry. Generic selective vectorization of arbitrary scientific imagery is not established.
- Raster assets/source preservation: implemented; the controlled UAV result retained three exact source crops. Whole-asset editing is not independent object segmentation or native chart data.
- Irregular object instance separation, transparent layers and occlusion-aware reconstruction: planned.
- Molecules and true three-dimensional scientific objects: planned; a 2D layer or stylized drawing is not chemical/3D reconstruction.

## Scientific safety across directions

Do not fabricate obscured geometry, chemical structures, measurements, data or labels. Preserve safe evidence or fail closed. More compute does not authorize guessing. Any future image-generation/extra-provider/refinement call needs explicit account, transmission and budget approval; none is run by this README update.

The narrow Research Preview is separately scoped. PHASE_7_ENTRY_GATE = FAIL_UNCHANGED is preserved.

