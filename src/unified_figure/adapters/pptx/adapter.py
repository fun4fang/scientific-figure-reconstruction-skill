from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import escape
from pathlib import Path
from unified_figure.model.text_authoring import get_text_authoring, text_inner_bounds
from unified_figure.model.semantic_attachment import get_attachment, SITES
import json
import posixpath
import zipfile
from xml.etree import ElementTree as ET

from unified_figure.adapters._assets import artifact_hash, resolve_asset
from unified_figure.adapters._colors import color_hex
from unified_figure.adapters._layout import component_paint_key, fit_text_to_bbox, is_coherent_preserved_panel, is_verified_native_label, rich_label_lines, preset_text_rect_inset
from unified_figure.adapters._relations import relation_path, orthogonal_connector_preset
from unified_figure.adapters.config import RendererConfig
from unified_figure.adapters.contracts import (
    AdapterRealizationResult,
    AdapterValidationResult,
    BackendCapabilityProfile,
    RendererAdapter,
)
from unified_figure.model.enums import (
    AttachmentIntent,
    CostClass,
    EvidenceSourceType,
    RealizationStatus,
    RelationDirection,
    RepresentationType,
    RouteType,
    RuntimeVerification,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.geometry import BBox, ModelValidationError
from unified_figure.model.records import Component, EvidenceRecord, Relation, RendererRealization


PPTX_ADAPTER_VERSION = "pptx-adapter-v0.2"
PPTX_CAPABILITY_PROFILE_VERSION = "pptx-capabilities-v0.2"

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_EMU_PER_INCH = 914400
_SLIDE_WIDTH = 10 * _EMU_PER_INCH
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_OFFICE_TEMPLATE_PATH = Path(__file__).with_name("blank_office_v16.pptx")


def _xml(value: str) -> str:
    return escape(value, quote=True)


def _hex_color(value: str | None, default: str) -> str:
    return color_hex(value, default)


def _metadata(canonical_id: str, representation: RepresentationType, **extra: str) -> str:
    values = {"canonical_id": canonical_id, "representation": representation.value, **extra}
    return ";".join(f"uf:{key}={value}" for key, value in sorted(values.items()))


@dataclass(frozen=True, slots=True)
class _SlideObject:
    canonical_id: str
    realization_id: str
    backend_object_id: str
    representation: RepresentationType
    xml: str
    runtime_verification: RuntimeVerification
    warnings: tuple[str, ...]


class PptxRendererAdapter(RendererAdapter):
    """First-party, standard-library OOXML adapter for the Phase 5B MVP subset."""

    def __init__(
        self,
        output_path: str | Path,
        *,
        source_root: str | Path,
        artifact_ref: str | None = None,
        config: RendererConfig | None = None,
    ) -> None:
        self.output_path = Path(output_path)
        self.source_root = Path(source_root)
        self.artifact_ref = artifact_ref or f"artifacts/{self.output_path.name}"
        self.config = config or RendererConfig(
            adapter_version=PPTX_ADAPTER_VERSION,
            capability_profile_version=PPTX_CAPABILITY_PROFILE_VERSION,
            render_policy_version="phase5f-capability-isolated-ooxml-v0.2",
            font_policy="canonical-family-or-Arial;no-font-install",
            asset_policy="embed-source-verified-png;deduplicate-by-sha256",
            relation_policy="straight-native-connector;unsupported-routes-isolated-with-evidence",
        )

    def capabilities(self) -> BackendCapabilityProfile:
        return BackendCapabilityProfile(
            backend_id="unified-figure-pptx",
            supports_native_text=True,
            supports_simple_shape=True,
            supports_native_relation=True,
            supports_vector_asset=False,
            supports_exact_raster=True,
            supports_generated_asset=True,
            supports_runtime_attachment=True,
            supports_editable_chart=False,
            supports_alpha=True,
            optional_dependency=False,
            cost_class=CostClass.LOW,
        )

    def validate_input(self, figure: CanonicalFigure) -> AdapterValidationResult:
        messages: list[str] = []
        try:
            figure.validate()
        except ValueError as exc:
            return AdapterValidationResult(False, (str(exc),))
        supported = self.capabilities().supported_representations()
        assets = {item.asset_id: item for item in figure.assets}
        for component in figure.components:
            if component.representation is RepresentationType.NATIVE_TEXT:
                try:
                    get_text_authoring(figure,component)
                except (ValueError,KeyError,TypeError) as exc:
                    messages.append(str(exc))
            if component.representation not in supported:
                messages.append(f"unsupported representation {component.representation.value} on {component.component_id}")
            if component.representation is RepresentationType.NATIVE_SIMPLE_SHAPE:
                shape = self._shape_kind(component)
                if shape not in {"rect", "roundRect", "ellipse", "line"}:
                    messages.append(f"unsupported simple shape {shape} on {component.component_id}")
            if component.representation in {
                RepresentationType.EXACT_RASTER_ASSET,
                RepresentationType.RASTER_PRESERVE_COMPLEX,
                RepresentationType.GENERATED_COMPLEX_ASSET,
            }:
                asset = assets.get(component.asset_ref or "")
                if asset is None:
                    messages.append(f"missing asset mapping on {component.component_id}")
                else:
                    try:
                        path, _, _ = resolve_asset(asset, self.source_root)
                        if path.suffix.lower() != ".png":
                            messages.append(f"PPTX MVP accepts PNG raster assets only: {asset.asset_id}")
                    except ValueError as exc:
                        messages.append(str(exc))
        for relation in figure.relations:
            try:
                get_attachment(figure, relation)
            except (ValueError, KeyError, TypeError) as exc:
                messages.append(str(exc))
            if relation.representation is not RepresentationType.NATIVE_RELATION:
                messages.append(f"unsupported relation representation on {relation.relation_id}")
        return AdapterValidationResult(not messages, tuple(messages))

    def realize(self, figure: CanonicalFigure) -> AdapterRealizationResult:
        validation = self.validate_input(figure)
        if not validation.valid:
            raise ModelValidationError("PPTX adapter rejected input: " + "; ".join(validation.messages))
        from unified_figure.file_boundary import checked_absolute
        self.output_path = checked_absolute(self.output_path)
        if self.output_path.exists():
            raise ValueError("Writer output already exists; use a fresh path")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        shape_ids: dict[str, int] = {}
        objects: list[_SlideObject] = []
        media_by_hash: dict[str, tuple[str, bytes]] = {}
        media_rel_by_hash: dict[str, str] = {}
        isolated_realizations: list[RendererRealization] = []
        adapter_warnings: list[str] = []
        next_shape_id = 2

        for sequence, component in enumerate(sorted(figure.components, key=component_paint_key)):
            shape_id = next_shape_id
            next_shape_id += 1
            shape_ids[component.component_id] = shape_id
            realization_id = make_stable_id(figure.figure_id, "pptx-realization", sequence, component.component_id)
            backend_id = f"pptx-slide1-shape-{shape_id}"
            if component.representation is RepresentationType.NATIVE_TEXT:
                xml = self._text_xml(figure, component, shape_id, backend_id)
            elif component.representation is RepresentationType.NATIVE_SIMPLE_SHAPE:
                xml = self._shape_xml(figure, component, shape_id, backend_id)
            else:
                asset = next(item for item in figure.assets if item.asset_id == component.asset_ref)
                _, data, digest = resolve_asset(asset, self.source_root)
                if digest not in media_by_hash:
                    media_name = f"image{len(media_by_hash) + 1}.png"
                    media_by_hash[digest] = (media_name, data)
                    media_rel_by_hash[digest] = f"rId{len(media_by_hash) + 1}"
                media_name, _ = media_by_hash[digest]
                rel_id = media_rel_by_hash[digest]
                xml = self._picture_xml(figure, component, shape_id, backend_id, rel_id, asset.asset_id, digest)
            objects.append(
                _SlideObject(
                    component.component_id,
                    realization_id,
                    backend_id,
                    component.representation,
                    xml,
                    RuntimeVerification.PASS,
                    (),
                )
            )

        relation_offset = len(objects)
        for sequence, relation in enumerate(figure.relations):
            if relation.route_type not in {RouteType.STRAIGHT, RouteType.ELBOW}:
                realization_id = make_stable_id(
                    figure.figure_id,
                    "pptx-relation-isolation",
                    sequence,
                    relation.relation_id,
                )
                warning = (
                    f"UNSUPPORTED_REALIZATION:relation={relation.relation_id};"
                    f"route={relation.route_type.value};canonical-semantics-preserved"
                )
                isolated_realizations.append(
                    RendererRealization(
                        realization_id=realization_id,
                        canonical_object_id=relation.relation_id,
                        contributing_canonical_object_ids=(),
                        backend=self.capabilities().backend_id,
                        backend_object_id=f"pptx-unsupported-{relation.relation_id}",
                        representation_used=relation.representation,
                        artifact_ref=self.artifact_ref,
                        status=RealizationStatus.UNSUPPORTED,
                        runtime_verification=RuntimeVerification.NOT_APPLICABLE,
                        warnings=(warning, "REVIEW_REQUIRED"),
                    )
                )
                adapter_warnings.append(warning)
                continue
            shape_id = next_shape_id
            next_shape_id += 1
            realization_id = make_stable_id(
                figure.figure_id, "pptx-relation-realization", sequence, relation.relation_id
            )
            backend_id = f"pptx-slide1-connector-{shape_id}"
            xml, attachment = self._connector_xml(
                relation,
                shape_id,
                backend_id,
                shape_ids[relation.source_component_id],
                shape_ids[relation.target_component_id],
                figure,
            )
            objects.append(
                _SlideObject(
                    relation.relation_id,
                    realization_id,
                    backend_id,
                    relation.representation,
                    xml,
                    RuntimeVerification.PASS,
                    (f"ATTACHMENT_STATUS={attachment}",),
                )
            )

        # A coherent panel fallback is deliberately unchanged source content.
        # Keep editable native objects underneath it, but place the explicit
        # source-preserved region above relations/text to avoid visible
        # duplication or destructive masking.
        coherent_ids = {
            component.component_id
            for component in figure.components
            if is_coherent_preserved_panel(component)
        }
        generated_ids = {
            component.component_id
            for component in figure.components
            if component.representation is RepresentationType.GENERATED_COMPLEX_ASSET
        }
        verified_ids={c.component_id for c in figure.components if is_verified_native_label(c)}
        if coherent_ids:
            # A reviewed generated component is a bounded transparent overlay
            # on top of the coherent Phase-6C safety net.  Keep it above the
            # panel while preserving every unrelated object below that panel.
            objects = [
                item
                for item in objects
                if item.canonical_id not in coherent_ids and item.canonical_id not in generated_ids and item.canonical_id not in verified_ids
            ] + [item for item in objects if item.canonical_id in coherent_ids] + [
                item for item in objects if item.canonical_id in generated_ids
            ] + [item for item in objects if item.canonical_id in verified_ids]

        entries = self._package_entries(figure, objects, media_by_hash, media_rel_by_hash)
        self._write_zip(entries)
        digest = artifact_hash(self.output_path)
        result = AdapterRealizationResult(
            realizations=tuple(
                RendererRealization(
                    realization_id=item.realization_id,
                    canonical_object_id=item.canonical_id,
                    contributing_canonical_object_ids=(),
                    backend=self.capabilities().backend_id,
                    backend_object_id=item.backend_object_id,
                    representation_used=item.representation,
                    artifact_ref=self.artifact_ref,
                    status=RealizationStatus.VALIDATED,
                    runtime_verification=item.runtime_verification,
                    warnings=item.warnings,
                )
                for item in objects
            )
            + tuple(isolated_realizations),
            artifact_refs=(self.artifact_ref,),
            warnings=tuple(adapter_warnings),
            artifact_hashes=((self.artifact_ref, digest),),
            adapter_config_hash=self.config.config_hash(),
            runtime_info={
                "output_path": str(self.output_path.resolve()),
                "slide_count": "1",
                "shape_count": str(len(figure.components)),
                "connector_count": str(len(objects) - relation_offset),
                "unsupported_relation_count": str(len(isolated_realizations)),
                "embedded_asset_count": str(len(media_by_hash)),
                "external_calls": "0",
            },
        )
        output_validation = self.validate_output(result)
        if not output_validation.valid:
            raise ModelValidationError("PPTX output validation failed: " + "; ".join(output_validation.messages))
        return result

    def validate_output(self, result: AdapterRealizationResult) -> AdapterValidationResult:
        messages: list[str] = []
        path_text = result.runtime_info.get("output_path")
        if not path_text:
            return AdapterValidationResult(False, ("missing output_path runtime info",))
        path = Path(path_text)
        try:
            with zipfile.ZipFile(path) as package:
                bad = package.testzip()
                if bad:
                    messages.append(f"corrupt ZIP member: {bad}")
                names = set(package.namelist())
                required = {
                    "[Content_Types].xml",
                    "ppt/presentation.xml",
                    "ppt/slides/slide1.xml",
                    "ppt/slides/_rels/slide1.xml.rels",
                }
                missing = required - names
                if missing:
                    messages.append("missing package members: " + ",".join(sorted(missing)))
                slide = ET.fromstring(package.read("ppt/slides/slide1.xml"))
                ids: set[str] = set()
                mappings: dict[str, ET.Element] = {}
                for node in slide.findall(".//p:cNvPr", _NS):
                    node_id = node.attrib.get("id", "")
                    if node_id in ids:
                        messages.append(f"duplicate PowerPoint object ID {node_id}")
                    ids.add(node_id)
                    metadata = self._parse_metadata(node.attrib.get("descr", ""))
                    if "canonical_id" in metadata:
                        mappings[metadata["canonical_id"]] = node
                expected = {
                    item.canonical_object_id
                    for item in result.realizations
                    if item.status in {RealizationStatus.REALIZED, RealizationStatus.VALIDATED}
                }
                if set(mappings) != expected:
                    messages.append("canonical realization mapping is incomplete")
                for cxn in slide.findall(".//p:cxnSp", _NS):
                    c_nv = cxn.find("./p:nvCxnSpPr/p:cNvCxnSpPr", _NS)
                    c_pr = cxn.find("./p:nvCxnSpPr/p:cNvPr", _NS)
                    metadata = self._parse_metadata(c_pr.attrib.get("descr", "")) if c_pr is not None else {}
                    if metadata.get("attachment") == "ATTACHED_VERIFIED":
                        if c_nv is None or c_nv.find("a:stCxn", _NS) is None or c_nv.find("a:endCxn", _NS) is None:
                            messages.append(f"connector {metadata.get('canonical_id', '?')} lost attachment mapping")
                    if not {"source_id", "target_id", "direction", "relation_type"}.issubset(metadata):
                        messages.append(f"connector {metadata.get('canonical_id', '?')} lost semantic mapping")
                rels = ET.fromstring(package.read("ppt/slides/_rels/slide1.xml.rels"))
                targets = [item.attrib.get("Target", "") for item in rels.findall(f"{{{_REL_NS}}}Relationship")]
                for target in targets:
                    if target.startswith("../media/") and f"ppt/media/{target.rsplit('/', 1)[-1]}" not in names:
                        messages.append(f"missing embedded asset {target}")
                for rel_name in sorted(name for name in names if name.endswith(".rels")):
                    rel_root = ET.fromstring(package.read(rel_name))
                    if rel_name == "_rels/.rels":
                        base = ""
                    else:
                        base = posixpath.dirname(posixpath.dirname(rel_name))
                    for relationship in rel_root.findall(f"{{{_REL_NS}}}Relationship"):
                        if relationship.attrib.get("TargetMode") == "External":
                            continue
                        target = relationship.attrib.get("Target", "")
                        resolved = posixpath.normpath(posixpath.join(base, target)).lstrip("/")
                        if resolved not in names:
                            messages.append(f"dangling package relationship {rel_name} -> {target}")
                inventory = {
                    "shape_count": len(slide.findall(".//p:sp", _NS)) + len(slide.findall(".//p:pic", _NS)),
                    "connector_count": len(slide.findall(".//p:cxnSp", _NS)),
                }
                for key, actual in inventory.items():
                    if key in result.runtime_info and int(result.runtime_info[key]) != actual:
                        messages.append(f"{key} inventory mismatch")
        except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            messages.append(f"invalid PPTX package: {exc}")
        if result.adapter_config_hash != self.config.config_hash():
            messages.append("adapter config hash mismatch")
        return AdapterValidationResult(not messages, tuple(messages))

    def collect_runtime_evidence(self, result: AdapterRealizationResult) -> tuple[EvidenceRecord, ...]:
        digest = result.artifact_hashes[0][1]
        return (
            EvidenceRecord(
                evidence_id="evidence-pptx-renderer-phase5b",
                source_type=EvidenceSourceType.RENDERER,
                source_hash=digest,
                producer=PPTX_ADAPTER_VERSION,
                operation="realize-and-validate-ooxml",
                input_refs=tuple(item.canonical_object_id for item in result.realizations),
                output_refs=result.artifact_refs,
                run_index=0,
                tool_runtime_info={
                    "adapter_config_hash": result.adapter_config_hash,
                    "runtime": "python-stdlib-ooxml",
                    "external_calls": "0",
                    "powerpoint_runtime": "NOT_RUN",
                },
                notes="ZIP, relationships, inventory, embedded assets, mappings, and connector OOXML verified.",
            ),
        )

    @staticmethod
    def _parse_metadata(text: str) -> dict[str, str]:
        result = {}
        for part in text.split(";"):
            if part.startswith("uf:") and "=" in part:
                key, value = part[3:].split("=", 1)
                result[key] = value
        return result

    @staticmethod
    def _shape_kind(component: Component) -> str:
        role = component.semantic_role.lower().replace("-", "_")
        return {
            "rectangle": "rect",
            "rect": "rect",
            "simple_shape": "rect",
            "panel": "rect",
            "rounded_rectangle": "roundRect",
            "roundrect": "roundRect",
            "ellipse": "ellipse",
            "circle": "ellipse",
            "line": "line",
        }.get(role, role)

    @staticmethod
    def _bounds(figure: CanonicalFigure, bbox: BBox) -> tuple[int, int, int, int]:
        scale = _SLIDE_WIDTH / figure.input_geometry.decoded_width_px
        x = round(bbox.x * scale)
        y = round(bbox.y * scale)
        cx = round(bbox.width * scale)
        cy = round(bbox.height * scale)
        return x, y, cx, cy

    def _text_xml(self, figure: CanonicalFigure, component: Component, shape_id: int, name: str) -> str:
        authoring=get_text_authoring(figure,component)
        container=authoring.get('container') if authoring else None
        bounds=BBox(**authoring['text_bounds']) if authoring else component.geometry.bbox
        outer=BBox(**container['bbox']) if container else bounds
        x, y, cx, cy = self._bounds(figure, outer)
        if is_verified_native_label(component):
            import json
            from dataclasses import replace
            layout=json.loads(figure.metadata['native.label.'+component.component_id])
            b=component.geometry.bbox
            x,y,cx,cy=self._bounds(figure,replace(b,x=b.x+layout.get('pptx.dx',0),y=b.y+layout.get('pptx.dy',0)))
        text = figure.metadata.get(f"text.{component.component_id}", component.semantic_role)
        font = component.style.font.family if component.style.font and component.style.font.family else "Arial"
        fitted = fit_text_to_bbox(text, text_inner_bounds(authoring) if authoring else bounds, component.style.font_size)
        if authoring and 'autofit' in authoring and component.style.font_size is not None:
            from dataclasses import replace
            fitted=replace(fitted,lines=tuple(text.split('\n')),font_size_px=component.style.font_size,rotation_degrees=0)
        if is_verified_native_label(component):
            from dataclasses import replace
            fitted=replace(fitted,lines=(text,),font_size_px=component.style.font_size,rotation_degrees=0,fits_bbox=True)
        size_points = fitted.font_size_px * 720 / figure.input_geometry.decoded_width_px
        size = max(100, round(size_points * 100))
        color = _hex_color(component.style.fill, "000000")
        align = {"center": "ctr", "right": "r", "left": "l"}.get((component.style.text_alignment or "left").lower(), "l")
        descr = _metadata(
            component.component_id,
            component.representation,
            object_type="text",
            text_fit="PASS" if fitted.fits_bbox else "FAIL",
            text_rotation=str(fitted.rotation_degrees),
        )
        bold = component.style.font is not None and component.style.font.weight.lower() in {"bold", "600", "700", "800", "900"}
        italic = component.style.font is not None and component.style.font.italic
        run_properties = (
            f'<a:rPr lang="en-US" sz="{size}" b="{1 if bold else 0}" i="{1 if italic else 0}">'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            f'<a:latin typeface="{_xml(font)}"/></a:rPr>'
        )
        paragraphs = "".join(
            f'<a:p><a:pPr algn="{align}"/><a:r>{run_properties}<a:t>{_xml(line)}</a:t></a:r>'
            '<a:endParaRPr lang="en-US"/></a:p>'
            for line in fitted.lines
        )
        vertical = ' vert="vert270"' if fitted.rotation_degrees else ""
        preset='rect';geometry='<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>'
        body=f'<a:bodyPr wrap="none" anchor="ctr" lIns="0" rIns="0" tIns="0" bIns="0"{vertical}/>'
        if authoring:
            inner=text_inner_bounds(authoring);scale=_SLIDE_WIDTH/figure.input_geometry.decoded_width_px
            insets={'lIns':inner.x-outer.x,'tIns':inner.y-outer.y,
                    'rIns':outer.x+outer.width-inner.x-inner.width,'bIns':outer.y+outer.height-inner.y-inner.height}
            wrap='square' if authoring.get('wrap')=='WRAP' else 'none'
            inset_xml=' '.join(f'{key}="{round(value*scale)}"' for key,value in insets.items())
            fit={'NONE':'<a:noAutofit/>','SHRINK_TO_FIT':'<a:normAutofit/>'}.get(authoring.get('autofit'),'')
            body=f'<a:bodyPr wrap="{wrap}" anchor="{authoring.get("vertical_anchor","ctr")}" {inset_xml}>{fit}</a:bodyPr>'
        if container:
            preset=container['preset'];adjustments=''.join(f'<a:gd name="{_xml(v["name"])}" fmla="{_xml(v["fmla"])}"/>' for v in container.get('adjustments',[]))
            fill='<a:noFill/>' if container.get('fill') in (None,'none') else f'<a:solidFill><a:srgbClr val="{_hex_color(container["fill"],"FFFFFF")}"/></a:solidFill>'
            stroke='<a:noFill/>' if container.get('stroke') in (None,'none') else f'<a:solidFill><a:srgbClr val="{_hex_color(container["stroke"],"000000")}"/></a:solidFill>'
            width=round(container.get('stroke_width_px',0)*_SLIDE_WIDTH/figure.input_geometry.decoded_width_px)
            geometry=f'<a:prstGeom prst="{preset}"><a:avLst>{adjustments}</a:avLst></a:prstGeom>{fill}<a:ln w="{width}">{stroke}</a:ln>'
        if authoring and authoring.get('paragraphs'):
            # One real shape/text body; rich labels retain movement/attachment.
            scale=_SLIDE_WIDTH/figure.input_geometry.decoded_width_px
            lines=tuple(rich_label_lines(authoring,
                fit_to_container=bool(figure.metadata.get('visual.reauthoring.version'))))
            inset=preset_text_rect_inset(container)
            # PowerPoint ignores first-paragraph spcBef; position it via bodyPr.
            # Cancel the preset's inscribed text rectangle, not source padding.
            first_top=lines[0]['top']-outer.y
            body=(f'<a:bodyPr wrap="none" anchor="t" lIns="{round(-inset*scale)}" '
                  f'rIns="{round(-inset*scale)}" tIns="{round((first_top-inset)*scale)}" '
                  f'bIns="{round(-inset*scale)}"><a:noAutofit/></a:bodyPr>')
            paragraphs='';cursor=first_top
            for index,item in enumerate(lines):
                block=item['block'];b=item['bounds'];fs=block['font_size_px'];lh=item['height']
                top=item['top']-outer.y
                before=max(0,top-cursor) if index else 0
                align={'left':'l','center':'ctr','right':'r'}[block['alignment']]
                size=round(fs*720/figure.input_geometry.decoded_width_px*100)
                px_to_pt100=720/figure.input_geometry.decoded_width_px*100
                props=f'<a:pPr algn="{align}" marL="{round((b.x-outer.x)*scale)}" marR="{round((outer.x+outer.width-b.x-b.width)*scale)}"><a:lnSpc><a:spcPts val="{round(lh*px_to_pt100)}"/></a:lnSpc><a:spcBef><a:spcPts val="{round(before*px_to_pt100)}"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft></a:pPr>'
                run=f'<a:rPr lang="en-US" sz="{size}" b="{1 if block["font_weight"]=="bold" else 0}"><a:solidFill><a:srgbClr val="{_hex_color(block["text_color"],"000000")}"/></a:solidFill><a:latin typeface="{_xml(block["font_family"])}"/></a:rPr>'
                paragraphs+=f'<a:p>{props}<a:r>{run}<a:t>{_xml(item["text"])}</a:t></a:r><a:endParaRPr sz="{size}"/></a:p>'
                cursor=top+lh
        return (
            f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{_xml(name)}" descr="{_xml(descr)}"/>'
            + ('<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>' if container and figure.metadata.get('visual.reauthoring.version') else '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>')
            +
            f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'{geometry}</p:spPr>'
            f'<p:txBody>{body}'
            f'<a:lstStyle/>{paragraphs}</p:txBody></p:sp>'
        )

    def _shape_xml(self, figure: CanonicalFigure, component: Component, shape_id: int, name: str) -> str:
        x, y, cx, cy = self._bounds(figure, component.geometry.bbox)
        shape = self._shape_kind(component)
        descr = _metadata(component.component_id, component.representation, object_type="shape", shape=shape)
        fill = _hex_color(component.style.fill, "FFFFFF")
        stroke = _hex_color(component.style.stroke, "000000")
        width = max(0, round(component.style.stroke_width * 12700))
        alpha = max(0, min(100000, round(component.style.opacity * 100000)))
        return (
            f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{_xml(name)}" descr="{_xml(descr)}"/>'
            '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>'
            f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="{shape}"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:srgbClr val="{fill}"><a:alpha val="{alpha}"/></a:srgbClr></a:solidFill>'
            f'<a:ln w="{width}"><a:solidFill><a:srgbClr val="{stroke}"><a:alpha val="{alpha}"/></a:srgbClr></a:solidFill></a:ln>'
            '</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
        )

    def _picture_xml(
        self,
        figure: CanonicalFigure,
        component: Component,
        shape_id: int,
        name: str,
        rel_id: str,
        asset_id: str,
        source_hash: str,
    ) -> str:
        x, y, cx, cy = self._bounds(figure, component.geometry.bbox)
        descr = _metadata(
            component.component_id,
            component.representation,
            object_type="picture",
            asset_id=asset_id,
            source_hash=source_hash,
        )
        return (
            f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="{_xml(name)}" descr="{_xml(descr)}"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        )

    def _connector_xml(
        self,
        relation: Relation,
        shape_id: int,
        name: str,
        source_shape_id: int,
        target_shape_id: int,
        figure: CanonicalFigure,
    ) -> tuple[str, str]:
        components = {item.component_id: item for item in figure.components}
        source = components[relation.source_component_id].geometry.bbox
        target = components[relation.target_component_id].geometry.bbox
        path = relation_path(relation, source, target,
                             align_orthogonal=bool(figure.metadata.get('visual.reauthoring.version')))
        x1, y1 = path.points[0].x, path.points[0].y
        x2, y2 = path.points[-1].x, path.points[-1].y
        scale = _SLIDE_WIDTH / figure.input_geometry.decoded_width_px
        x, y = round(min(x1, x2) * scale), round(min(y1, y2) * scale)
        cx, cy = max(1, round(abs(x2 - x1) * scale)), max(1, round(abs(y2 - y1) * scale))
        flips = (" flipH=\"1\"" if x2 < x1 else "") + (" flipV=\"1\"" if y2 < y1 else "")
        attached = relation.attachment_intent is AttachmentIntent.NATIVE_ATTACHMENT
        semantic = get_attachment(figure, relation)
        source_site = SITES[semantic['source_site']] if semantic else 0
        target_site = SITES[semantic['target_site']] if semantic else 0
        attachment = "ATTACHED_VERIFIED" if attached else "VISUAL_ONLY"
        connections = (
            f'<a:stCxn id="{source_shape_id}" idx="{source_site}"/><a:endCxn id="{target_shape_id}" idx="{target_site}"/>'
            if attached
            else ""
        )
        descr = _metadata(
            relation.relation_id,
            relation.representation,
            object_type="relation",
            source_id=relation.source_component_id,
            target_id=relation.target_component_id,
            direction=relation.direction.value,
            relation_type=relation.relation_type.value,
            attachment=attachment,
            route_type=relation.route_type.value,
            route_source=path.derived_from,
        )
        stroke = _hex_color(relation.visual_style.stroke, "000000")
        width = max(0, round(relation.visual_style.stroke_width * 12700))
        if figure.metadata.get('visual.reauthoring.version'):
            width = max(0, round(relation.visual_style.stroke_width * scale))
        head = relation.visual_style.arrowhead_start or "none"
        tail = relation.visual_style.arrowhead_end or (
            "triangle" if relation.direction in {RelationDirection.SOURCE_TO_TARGET, RelationDirection.BIDIRECTIONAL} else "none"
        )
        geometry = '<a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
        if relation.route_type is RouteType.ELBOW and figure.metadata.get('visual.reauthoring.version'):
            mapped = orthogonal_connector_preset(path.points, scale)
            x,y,cx,cy = (mapped[k] for k in ('x','y','cx','cy'))
            flips = (' flipH="1"' if mapped['flip_h'] else '') + (' flipV="1"' if mapped['flip_v'] else '')
            if mapped['rotation']: flips += f' rot="{mapped["rotation"]}"'
            guide = f'<a:gd name="adj1" fmla="val {mapped["adjustment"]}"/>' if mapped['preset']=='bentConnector3' else ''
            geometry = f'<a:prstGeom prst="{mapped["preset"]}"><a:avLst>{guide}</a:avLst></a:prstGeom>'
        elif relation.route_type is RouteType.ELBOW:
            interior = path.points[1] if len(path.points) > 2 else path.points[0]
            if abs(x2 - x1) >= abs(y2 - y1):
                adjustment = round(
                    abs(interior.x - min(x1, x2)) / max(1.0, abs(x2 - x1)) * 100000
                )
            else:
                adjustment = round(
                    abs(interior.y - min(y1, y2)) / max(1.0, abs(y2 - y1)) * 100000
                )
            adjustment = max(0, min(100000, adjustment))
            geometry = (
                '<a:prstGeom prst="bentConnector3"><a:avLst>'
                f'<a:gd name="adj1" fmla="val {adjustment}"/>'
                '</a:avLst></a:prstGeom>'
            )
        xml = (
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{shape_id}" name="{_xml(name)}" descr="{_xml(descr)}"/>'
            f'<p:cNvCxnSpPr>{connections}</p:cNvCxnSpPr><p:nvPr/></p:nvCxnSpPr><p:spPr>'
            f'<a:xfrm{flips}><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'{geometry}'
            f'<a:ln w="{width}"><a:solidFill><a:srgbClr val="{stroke}"/></a:solidFill>'
            f'<a:headEnd type="{_xml(head)}"/><a:tailEnd type="{_xml(tail)}"/></a:ln>'
            '</p:spPr></p:cxnSp>'
        )
        return xml, attachment

    def _package_entries(
        self,
        figure: CanonicalFigure,
        objects: list[_SlideObject],
        media_by_hash: dict[str, tuple[str, bytes]],
        media_rel_by_hash: dict[str, str],
    ) -> dict[str, bytes]:
        slide_height = round(_SLIDE_WIDTH * figure.input_geometry.decoded_height_px / figure.input_geometry.decoded_width_px)
        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            + "".join(item.xml for item in objects)
            + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
        )
        slide_rels = [
            '<Relationship Id="rId100" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        ]
        for digest, (media_name, _) in media_by_hash.items():
            slide_rels.append(
                f'<Relationship Id="{media_rel_by_hash[digest]}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/{media_name}"/>'
            )
        # The supporting OOXML package skeleton is a fixed one-slide blank
        # presentation created by Microsoft PowerPoint 16.  Keeping Office's
        # master/layout/theme/property parts avoids producing ZIP/XML-valid
        # files that PowerPoint itself refuses to open.  Only deterministic
        # figure-dependent parts are replaced below.
        entries = self._office_template_entries()
        content_types = entries["[Content_Types].xml"].decode("utf-8")
        if 'Extension="png"' not in content_types:
            content_types = content_types.replace(
                "</Types>",
                '<Default Extension="png" ContentType="image/png"/></Types>',
            )
        entries["[Content_Types].xml"] = content_types.encode("utf-8")
        entries.update({
            "ppt/presentation.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
                '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
                f'<p:sldSz cx="{_SLIDE_WIDTH}" cy="{slide_height}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
            ).encode(),
            "ppt/slides/slide1.xml": slide_xml.encode(),
            "ppt/slides/_rels/slide1.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(slide_rels)
                + '</Relationships>'
            ).encode(),
        })
        for _, (name, data) in media_by_hash.items():
            entries[f"ppt/media/{name}"] = data
        return entries

    @staticmethod
    def _office_template_entries() -> dict[str, bytes]:
        if not _OFFICE_TEMPLATE_PATH.is_file():
            raise ModelValidationError(f"missing frozen PowerPoint template: {_OFFICE_TEMPLATE_PATH}")
        with zipfile.ZipFile(_OFFICE_TEMPLATE_PATH) as package:
            if package.testzip() is not None:
                raise ModelValidationError("frozen PowerPoint template is corrupt")
            return {
                name: package.read(name)
                for name in package.namelist()
                if not name.endswith("/")
            }

    def _write_zip(self, entries: dict[str, bytes]) -> None:
        with zipfile.ZipFile(self.output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
            for name in sorted(entries):
                info = zipfile.ZipInfo(name, _ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                package.writestr(info, entries[name])

    @staticmethod
    def _slide_master_xml() -> str:
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
            '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>')

    @staticmethod
    def _slide_layout_xml() -> str:
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank">'
            '<p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')

    @staticmethod
    def _theme_xml() -> str:
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Unified Figure">'
            '<a:themeElements><a:clrScheme name="Unified"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F1F1F"/></a:dk2><a:lt2><a:srgbClr val="EEEEEE"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
            '<a:fontScheme name="Unified"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="Unified"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements>'
            '</a:theme>')
