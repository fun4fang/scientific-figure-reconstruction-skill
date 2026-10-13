"""Opt-in PNG/JPEG functional visual re-authoring using existing production paths."""
from pathlib import Path
import json
import time

from unified_figure.analysis import decode_reference_image
from unified_figure.analysis.reauthoring_intake import canonical_from_visual_authoring
from unified_figure.backends.registry import default_backend_registry
from unified_figure.routing import execute_fidelity_routing, get_quality_profile
from unified_figure.adapters.pptx.adapter import PptxRendererAdapter
from unified_figure.adapters.svg.adapter import SvgRendererAdapter
from unified_figure.visual import rasterize_pptx, rasterize_svg, build_comparison_artifacts


def reconstruct_image(image_path, output_dir, *, repository_root, correct_once=False,
                      model=None):
    """Explicit opt-in product entry: one initial inference, at most one render correction.

    A correction is never unconditional default behavior. Existing analysis/profile
    paths are not changed. Persisted slots are reused without additional model calls.
    """
    from unified_figure.analysis.visual_authoring import author_image, validate_proposal
    from PIL import Image
    from unified_figure.file_boundary import checked_absolute, checked_target
    folder=checked_target(repository_root, output_dir); analysis=folder/'analysis'
    image_path=checked_absolute(image_path)
    proposals=[analysis/'initial-proposal.json', analysis/'initial-schema-fixed-proposal.json']
    saved=next((p for p in proposals if p.is_file()), None)
    with Image.open(image_path) as image: size=image.size
    if saved:
        # Reuse requires the same input, not just the same output directory.
        import hashlib
        attempt=json.loads(saved.with_name(saved.name.replace('-proposal','-attempt')).read_text(encoding='utf-8'))
        if attempt['source_sha256'] != hashlib.sha256(Path(image_path).read_bytes()).hexdigest():
            raise ValueError('cached authoring source identity mismatch')
        proposal=validate_proposal(json.loads(saved.read_text(encoding='utf-8')), size)
    else:
        proposal=author_image(image_path, analysis, model=model)
    initial=folder/'initial'
    if not (initial/'runtime.json').is_file():
        realize_proposal(image_path, proposal, initial, repository_root=repository_root)
    if not correct_once: return initial
    saved=analysis/'correction-proposal.json'
    correction=(validate_proposal(json.loads(saved.read_text(encoding='utf-8')), size) if saved.is_file()
                else author_image(image_path, analysis, previous=proposal, model=model,
                    correction_images=[initial/'rendered_pptx.png', initial/'rendered_svg.png']))
    corrected=folder/'corrected'
    if not (corrected/'runtime.json').is_file():
        realize_proposal(image_path, correction, corrected, repository_root=repository_root)
    return corrected


def realize_proposal(image_path, proposal, output_dir, *, repository_root, render_outputs=True):
    from unified_figure.file_boundary import checked_absolute, checked_target
    root = checked_absolute(repository_root); image = checked_target(root, image_path)
    folder = checked_target(root, output_dir); folder.mkdir(parents=True, exist_ok=True)
    if (folder / "reconstructed.pptx").exists():
        raise ValueError("retain earlier outputs; use a distinct initial/corrected directory")
    started = time.perf_counter()
    source, geometry = decode_reference_image(root, image.relative_to(root).as_posix(), source_id="visual-reauthoring-source")
    figure = canonical_from_visual_authoring(proposal, source, geometry,
        repository_root=root, asset_dir=folder/"preserved")
    tick = time.perf_counter()
    routed = execute_fidelity_routing(figure, profile=get_quality_profile("BALANCED"),
        backend_registry=default_backend_registry(), repository_root=root, reference_path=image)
    routing_seconds = time.perf_counter()-tick
    state = routed.routed_state
    if any(d.generation_requested or d.photoshop_requested for d in routed.route_decisions):
        raise ValueError("optional generation/refinement is outside bounded workflow slice")
    (folder/"canonical.json").write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    (folder/"routing.json").write_text(json.dumps(routed.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    times = {"routing_seconds": routing_seconds}
    for ext, writer, raster in (("pptx", PptxRendererAdapter, rasterize_pptx), ("svg", SvgRendererAdapter, rasterize_svg)):
        tick = time.perf_counter(); path = folder/("reconstructed."+ext)
        result = writer(path, source_root=root).realize(state)
        times[ext+"_writer_seconds"] = time.perf_counter()-tick
        if not render_outputs:
            continue
        tick = time.perf_counter()
        raster(path, folder/("rendered_"+ext+".png"), width_px=geometry.decoded_width_px, height_px=geometry.decoded_height_px)
        times[ext+"_render_seconds"] = time.perf_counter()-tick
    if render_outputs:
        build_comparison_artifacts(image, folder/"rendered_pptx.png", folder/"comparison",
            width_px=geometry.decoded_width_px, height_px=geometry.decoded_height_px, label="Visual re-authoring PPTX")
        from shutil import copyfile
        copyfile(folder/"comparison/side_by_side.png", folder/"side_by_side.png")
    times["downstream_total_seconds"] = time.perf_counter()-started
    (folder/"runtime.json").write_text(json.dumps(times, indent=2), encoding="utf-8")
    return state, times


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image"); p.add_argument("proposal", help="cached proposal path, or AUTHOR for real image authoring"); p.add_argument("output")
    p.add_argument('--correct-once', action='store_true')
    p.add_argument("--repository-root", default=".")
    a = p.parse_args()
    if a.proposal == 'AUTHOR':
        reconstruct_image(a.image, a.output, repository_root=a.repository_root, correct_once=a.correct_once)
    else:
        realize_proposal(a.image, json.loads(Path(a.proposal).read_text(encoding="utf-8")), a.output,
                         repository_root=a.repository_root)
