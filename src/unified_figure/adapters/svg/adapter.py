from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
from unified_figure.model.text_authoring import get_text_authoring, text_inner_bounds
from unified_figure.model.semantic_attachment import get_attachment, endpoint_status
import re
from xml.etree import ElementTree as ET

from unified_figure.adapters._assets import artifact_hash, resolve_asset
from unified_figure.adapters._colors import svg_color
from unified_figure.adapters._layout import (
    component_paint_key,
    fit_text_to_bbox,
    is_coherent_preserved_panel,
    is_verified_native_label,
    text_anchor_x,
    rich_label_lines,
)
from unified_figure.adapters._relations import relation_path
from unified_figure.adapters.config import RendererConfig
from unified_figure.adapters.contracts import (
    AdapterRealizationResult,
    AdapterValidationResult,
    BackendCapabilityProfile,
    RendererAdapter,
)
from unified_figure.model.enums import (
    CostClass,
    EvidenceSourceType,
    RealizationStatus,
    RelationDirection,
    RepresentationType,
    RouteType,
    RuntimeVerification,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.geometry import ModelValidationError
from unified_figure.model.records import Component, EvidenceRecord, RendererRealization


SVG_ADAPTER_VERSION = "svg-adapter-v0.2"
SVG_CAPABILITY_PROFILE_VERSION = "svg-capabilities-v0.2"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def _safe_id(prefix: str, canonical_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]", "-", canonical_id)
    if not value or value[0].isdigit():
        value = "id-" + value
    return f"uf-{prefix}-{value}"


class SvgRendererAdapter(RendererAdapter):
    """Structured first-party SVG adapter with auditable canonical mappings."""

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
            adapter_version=SVG_ADAPTER_VERSION,
            capability_profile_version=SVG_CAPABILITY_PROFILE_VERSION,
            render_policy_version="phase5f-capability-isolated-svg-v0.2",
            font_policy="canonical-family-or-sans-serif;no-font-install",
            asset_policy="embed-hash-verified-source-assets;preserve-vector-structure",
            relation_policy="straight-semantic-line;unsupported-routes-isolated-with-evidence",
        )

    def capabilities(self) -> BackendCapabilityProfile:
        return BackendCapabilityProfile(
            backend_id="unified-figure-svg",
            supports_native_text=True,
            supports_simple_shape=True,
            supports_native_relation=True,
            supports_vector_asset=True,
            supports_exact_raster=True,
            supports_generated_asset=True,
            supports_runtime_attachment=False,
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
            if component.representation in {
                RepresentationType.VECTOR_ASSET,
                RepresentationType.EXACT_RASTER_ASSET,
                RepresentationType.RASTER_PRESERVE_COMPLEX,
                RepresentationType.GENERATED_COMPLEX_ASSET,
            }:
                asset = assets.get(component.asset_ref or "")
                if asset is None:
                    messages.append(f"missing asset mapping on {component.component_id}")
                else:
                    try:
                        path, data, _ = resolve_asset(asset, self.source_root)
                        if component.representation is RepresentationType.VECTOR_ASSET:
                            if path.suffix.lower() != ".svg":
                                messages.append(f"VECTOR_ASSET must reference SVG in Phase 5B: {asset.asset_id}")
                            else:
                                root = ET.fromstring(data)
                                if root.tag != _tag("svg"):
                                    messages.append(f"vector source is not an SVG root: {asset.asset_id}")
                                if root.findall(".//" + _tag("script")):
                                    messages.append(f"vector source contains script: {asset.asset_id}")
                    except (ValueError, ET.ParseError) as exc:
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
            raise ModelValidationError("SVG adapter rejected input: " + "; ".join(validation.messages))
        from unified_figure.file_boundary import checked_absolute
        self.output_path = checked_absolute(self.output_path)
        if self.output_path.exists():
            raise ValueError("Writer output already exists; use a fresh path")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element(
            _tag("svg"),
            {
                "id": _safe_id("figure", figure.figure_id),
                "viewBox": f"0 0 {figure.input_geometry.decoded_width_px} {figure.input_geometry.decoded_height_px}",
                "width": str(figure.input_geometry.decoded_width_px),
                "height": str(figure.input_geometry.decoded_height_px),
                "data-canonical-figure-id": figure.figure_id,
                "data-adapter-config-hash": self.config.config_hash(),
            },
        )
        defs = ET.SubElement(root, _tag("defs"))
        marker = ET.SubElement(
            defs,
            _tag("marker"),
            {
                "id": "uf-arrow-triangle",
                "viewBox": "0 0 10 10",
                "refX": "9",
                "refY": "5",
                "markerWidth": "6",
                "markerHeight": "6",
                "orient": "auto-start-reverse",
            },
        )
        ET.SubElement(marker, _tag("path"), {"d": "M 0 0 L 10 5 L 0 10 z", "fill": "context-stroke"})
        if figure.metadata.get('visual.reauthoring.version'):
            # Match the native connector's medium triangle, not a separate asset.
            # The actual tip sits on the semantic endpoint (no marker overshoot).
            marker.set('markerWidth','3');marker.set('markerHeight','3');marker.set('refX','10')
        group_layer = ET.SubElement(root, _tag("g"), {"id": "uf-semantic-groups", "data-layer": "semantic-groups"})
        for group in figure.groups:
            node = ET.SubElement(
                group_layer,
                _tag("g"),
                {
                    "id": _safe_id("group", group.group_id),
                    "data-canonical-group-id": group.group_id,
                    "data-member-component-ids": " ".join(group.member_component_ids),
                    "data-semantic-role": group.semantic_role,
                },
            )
            ET.SubElement(node, _tag("metadata")).text = "Authoritative membership is canonical, not visual DOM nesting."

        object_layer = ET.SubElement(root, _tag("g"), {"id": "uf-components", "data-layer": "components"})
        relation_layer = ET.SubElement(root, _tag("g"), {"id": "uf-relations", "data-layer": "relations"})
        preservation_layer = ET.SubElement(
            root,
            _tag("g"),
            {"id": "uf-coherent-source-preservation", "data-layer": "coherent-source-preservation"},
        )
        generated_layer = ET.SubElement(
            root,
            _tag("g"),
            {"id": "uf-generated-asset-overlays", "data-layer": "generated-asset-overlays"},
        )
        verified_native_layer = ET.SubElement(root, _tag('g'), {'id':'uf-verified-native-labels'})
        assets = {item.asset_id: item for item in figure.assets}
        realizations: list[RendererRealization] = []
        adapter_warnings: list[str] = []

        for sequence, component in enumerate(sorted(figure.components, key=component_paint_key)):
            realization_id = make_stable_id(figure.figure_id, "svg-realization", sequence, component.component_id)
            backend_id = _safe_id("component", component.component_id)
            attrs = {
                "id": backend_id,
                "data-canonical-id": component.component_id,
                "data-representation": component.representation.value,
                "data-semantic-role": component.semantic_role,
                "data-realization-id": realization_id,
            }
            bbox = component.geometry.bbox
            if component.representation is RepresentationType.NATIVE_TEXT:
                authoring=get_text_authoring(figure,component)
                if authoring:
                    bbox=text_inner_bounds(authoring)
                text = figure.metadata.get(f"text.{component.component_id}", component.semantic_role)
                fitted = fit_text_to_bbox(text, bbox, component.style.font_size)
                if authoring and authoring.get('autofit') == 'NONE' and figure.metadata.get('visual.reauthoring.version'):
                    from dataclasses import replace
                    fitted = replace(fitted, lines=tuple(text.split('\n')), font_size_px=component.style.font_size,
                                     rotation_degrees=0)
                alignment = (component.style.text_alignment or "left").lower()
                anchor_x = text_anchor_x(bbox, alignment)
                center_y = bbox.y + bbox.height / 2
                attrs.update(
                    {
                        "x": str(anchor_x),
                        "y": str(center_y),
                        "font-family": component.style.font.family if component.style.font and component.style.font.family else "sans-serif",
                        "font-size": f"{fitted.font_size_px:.4f}",
                        "fill": svg_color(component.style.fill, "000000"),
                        "text-anchor": {"center": "middle", "right": "end"}.get(alignment, "start"),
                        "dominant-baseline": "middle",
                        "data-text-fit": "PASS" if fitted.fits_bbox else "FAIL",
                        "data-text-rotation": str(fitted.rotation_degrees),
                    }
                )
                if component.style.font:
                    attrs["font-weight"] = component.style.font.weight or "normal"
                    if component.style.font.italic:
                        attrs["font-style"] = "italic"
                if fitted.rotation_degrees:
                    attrs["transform"] = (
                        f"rotate({fitted.rotation_degrees} "
                        f"{bbox.x + bbox.width / 2} {bbox.y + bbox.height / 2})"
                    )
                parent=verified_native_layer if is_verified_native_label(component) else object_layer
                # One canonical object keeps one stable DOM owner, even when a
                # standalone rich label is split into several rendered lines.
                # Line IDs are children, never replacement realization IDs.
                if authoring and (authoring.get('container') or authoring.get('paragraphs')):
                    parent=ET.SubElement(parent,_tag('g'),dict(attrs,id=backend_id))
                    if authoring.get('container'):
                        self._append_label_container(parent,authoring['container'])
                    attrs={k:v for k,v in attrs.items() if not k.startswith('data-')}
                    attrs['id']=backend_id+'-text'
                node = ET.SubElement(parent, _tag("text"), attrs)
                if authoring and authoring.get('paragraphs'):
                    parent.remove(node)
                    for block_index,item in enumerate(rich_label_lines(authoring,
                            fit_to_container=bool(figure.metadata.get('visual.reauthoring.version')))):
                        block=item['block'];b=item['bounds'];fs=block['font_size_px']
                        x=text_anchor_x(b,block['alignment']);y=item['center']
                        a=dict(attrs,id=backend_id+'-block-'+str(block_index),x=str(x),y=str(y),
                               fill=block['text_color'], **{'font-family':block['font_family'],'font-size':str(fs),
                               'font-weight':block['font_weight'],'text-anchor':{'left':'start','center':'middle','right':'end'}[block['alignment']]})
                        node=ET.SubElement(parent,_tag('text'),a)
                        ET.SubElement(node,_tag('tspan'),{'x':str(x),'y':str(y)}).text=item['text']
                    node=None
                line_height = fitted.font_size_px * 1.15
                first_y = center_y - (len(fitted.lines) - 1) * line_height / 2
                if authoring:
                    anchor=authoring.get('vertical_anchor','ctr')
                    if anchor=='t':first_y=bbox.y+line_height/2
                    elif anchor=='b':first_y=bbox.y+bbox.height-(len(fitted.lines)-0.5)*line_height
                for line_index, line in enumerate(fitted.lines if node is not None else ()):
                    tspan = ET.SubElement(
                        node,
                        _tag("tspan"),
                        {"x": str(anchor_x), "y": f"{first_y + line_index * line_height:.4f}"},
                    )
                    tspan.text = line
                if is_verified_native_label(component):
                    # Source-tested glyph baseline, without heuristic reflow.
                    attrs_key='native.label.'+component.component_id
                    import json
                    layout=json.loads(figure.metadata[attrs_key])
                    node.attrib.pop('dominant-baseline',None)
                    node.set('font-size',str(component.style.font_size))
                    node.set('text-rendering','geometricPrecision')
                    node.set('text-anchor','start')
                    node.set('x',str(layout['x']+layout.get('svg.dx',0))); node.set('y',str(layout['baseline']+layout.get('svg.dy',0)))
                    for child in list(node): node.remove(child)
                    node.text=text
            elif component.representation is RepresentationType.NATIVE_SIMPLE_SHAPE:
                self._append_shape(object_layer, component, attrs)
            elif component.representation is RepresentationType.VECTOR_ASSET:
                asset = assets[component.asset_ref]
                _, data, digest = resolve_asset(asset, self.source_root)
                source = ET.fromstring(data)
                self._namespace_source_ids(source, backend_id)
                attrs.update(
                    {
                        "x": str(bbox.x),
                        "y": str(bbox.y),
                        "width": str(bbox.width),
                        "height": str(bbox.height),
                        "data-asset-id": asset.asset_id,
                        "data-source-hash": digest,
                        "data-vector-preservation": "SOURCE_STRUCTURE",
                    }
                )
                node = ET.SubElement(object_layer, _tag("svg"), attrs)
                if "viewBox" in source.attrib:
                    node.set("viewBox", source.attrib["viewBox"])
                else:
                    node.set("viewBox", f"0 0 {source.attrib.get('width', bbox.width)} {source.attrib.get('height', bbox.height)}")
                for child in source:
                    node.append(deepcopy(child))
            else:
                asset = assets[component.asset_ref]
                _, data, digest = resolve_asset(asset, self.source_root)
                attrs.update(
                    {
                        "x": str(bbox.x),
                        "y": str(bbox.y),
                        "width": str(bbox.width),
                        "height": str(bbox.height),
                        "data-asset-id": asset.asset_id,
                        "data-source-hash": digest,
                        "href": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
                    }
                )
                parent = (
                    generated_layer
                    if component.representation is RepresentationType.GENERATED_COMPLEX_ASSET
                    else preservation_layer
                    if is_coherent_preserved_panel(component)
                    else object_layer
                )
                if parent is preservation_layer:
                    attrs["data-source-preservation-policy"] = "UNCHANGED_COHERENT_REGION"
                if parent is generated_layer:
                    attrs["data-generated-asset-policy"] = "REVIEWED_COMPONENT_OVERLAY"
                ET.SubElement(parent, _tag("image"), attrs)
            realizations.append(
                RendererRealization(
                    realization_id=realization_id,
                    canonical_object_id=component.component_id,
                    contributing_canonical_object_ids=(),
                    backend=self.capabilities().backend_id,
                    backend_object_id=backend_id,
                    representation_used=component.representation,
                    artifact_ref=self.artifact_ref,
                    status=RealizationStatus.VALIDATED,
                    runtime_verification=RuntimeVerification.PASS,
                    warnings=(),
                )
            )

        components = {item.component_id: item for item in figure.components}
        realized_relation_count = 0
        for sequence, relation in enumerate(figure.relations):
            realization_id = make_stable_id(figure.figure_id, "svg-relation-realization", sequence, relation.relation_id)
            backend_id = _safe_id("relation", relation.relation_id)
            if relation.route_type not in {RouteType.STRAIGHT, RouteType.ELBOW}:
                warning = (
                    f"UNSUPPORTED_REALIZATION:relation={relation.relation_id};"
                    f"route={relation.route_type.value};canonical-semantics-preserved"
                )
                realizations.append(
                    RendererRealization(
                        realization_id=realization_id,
                        canonical_object_id=relation.relation_id,
                        contributing_canonical_object_ids=(),
                        backend=self.capabilities().backend_id,
                        backend_object_id=f"svg-unsupported-{relation.relation_id}",
                        representation_used=relation.representation,
                        artifact_ref=self.artifact_ref,
                        status=RealizationStatus.UNSUPPORTED,
                        runtime_verification=RuntimeVerification.NOT_APPLICABLE,
                        warnings=(warning, "REVIEW_REQUIRED"),
                    )
                )
                adapter_warnings.append(warning)
                continue
            source = components[relation.source_component_id].geometry.bbox
            target = components[relation.target_component_id].geometry.bbox
            path = relation_path(relation, source, target,
                                 align_orthogonal=bool(figure.metadata.get('visual.reauthoring.version')))
            start, end = path.points[0], path.points[-1]
            x1, y1, x2, y2 = start.x, start.y, end.x, end.y
            attrs = {
                "id": backend_id,
                "data-canonical-id": relation.relation_id,
                "data-realization-id": realization_id,
                "data-representation": relation.representation.value,
                "data-source-id": relation.source_component_id,
                "data-target-id": relation.target_component_id,
                "data-direction": relation.direction.value,
                "data-relation-type": relation.relation_type.value,
                "data-route-type": relation.route_type.value,
                "data-route-source": path.derived_from,
                "stroke": svg_color(relation.visual_style.stroke, "000000"),
                "stroke-width": str(relation.visual_style.stroke_width),
                "fill": "none",
            }
            semantic = get_attachment(figure, relation)
            if semantic:
                attrs.update({"data-source-site": semantic['source_site'],
                    "data-target-site": semantic['target_site'],
                    "data-source-endpoint-status": endpoint_status(figure, components[relation.source_component_id]),
                    "data-target-endpoint-status": endpoint_status(figure, components[relation.target_component_id]),
                    "data-attachment-behavior": "STATIC_ONLY"})
            if relation.direction in {RelationDirection.SOURCE_TO_TARGET, RelationDirection.BIDIRECTIONAL} or relation.visual_style.arrowhead_end:
                attrs["marker-end"] = "url(#uf-arrow-triangle)"
            if relation.direction in {RelationDirection.TARGET_TO_SOURCE, RelationDirection.BIDIRECTIONAL} or relation.visual_style.arrowhead_start:
                attrs["marker-start"] = "url(#uf-arrow-triangle)"
            if relation.route_type is RouteType.ELBOW:
                attrs["points"] = " ".join(f"{point.x},{point.y}" for point in path.points)
                attrs["stroke-linejoin"] = "round"
                ET.SubElement(relation_layer, _tag("polyline"), attrs)
            else:
                attrs.update({"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)})
                ET.SubElement(relation_layer, _tag("line"), attrs)
            realized_relation_count += 1
            realizations.append(
                RendererRealization(
                    realization_id=realization_id,
                    canonical_object_id=relation.relation_id,
                    contributing_canonical_object_ids=(),
                    backend=self.capabilities().backend_id,
                    backend_object_id=backend_id,
                    representation_used=relation.representation,
                    artifact_ref=self.artifact_ref,
                    status=RealizationStatus.VALIDATED,
                    runtime_verification=RuntimeVerification.PASS,
                    warnings=("ATTACHMENT_STATUS=VISUAL_ONLY",),
                )
            )

        ET.indent(root, space="  ")
        self.output_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        digest = artifact_hash(self.output_path)
        result = AdapterRealizationResult(
            realizations=tuple(realizations),
            artifact_refs=(self.artifact_ref,),
            warnings=tuple(adapter_warnings),
            artifact_hashes=((self.artifact_ref, digest),),
            adapter_config_hash=self.config.config_hash(),
            runtime_info={
                "output_path": str(self.output_path.resolve()),
                "component_count": str(len(figure.components)),
                "relation_count": str(realized_relation_count),
                "unsupported_relation_count": str(len(adapter_warnings)),
                "external_calls": "0",
            },
        )
        output_validation = self.validate_output(result)
        if not output_validation.valid:
            raise ModelValidationError("SVG output validation failed: " + "; ".join(output_validation.messages))
        return result

    def validate_output(self, result: AdapterRealizationResult) -> AdapterValidationResult:
        messages: list[str] = []
        path_text = result.runtime_info.get("output_path")
        if not path_text:
            return AdapterValidationResult(False, ("missing output_path runtime info",))
        try:
            root = ET.parse(path_text).getroot()
            if root.tag != _tag("svg"):
                messages.append("root is not SVG")
            nodes = list(root.iter())
            ids = [node.attrib["id"] for node in nodes if "id" in node.attrib]
            if len(ids) != len(set(ids)):
                messages.append("SVG IDs are not unique")
            mappings = {
                node.attrib["data-canonical-id"]: node
                for node in nodes
                if "data-canonical-id" in node.attrib
            }
            expected = {
                item.canonical_object_id
                for item in result.realizations
                if item.status in {RealizationStatus.REALIZED, RealizationStatus.VALIDATED}
            }
            if set(mappings) != expected:
                messages.append("canonical mapping is incomplete")
            for item in result.realizations:
                if item.status not in {RealizationStatus.REALIZED, RealizationStatus.VALIDATED}:
                    continue
                node = mappings.get(item.canonical_object_id)
                if node is None or node.attrib.get("id") != item.backend_object_id:
                    messages.append(f"realization mapping mismatch for {item.canonical_object_id}")
            for node in root.findall(".//" + _tag("line")):
                if node.attrib.get('data-representation') == RepresentationType.NATIVE_SIMPLE_SHAPE.value:
                    continue
                required = {"data-source-id", "data-target-id", "data-direction", "data-relation-type"}
                if not required.issubset(node.attrib):
                    messages.append(f"relation {node.attrib.get('data-canonical-id', '?')} lost semantic metadata")
            for node in root.findall(".//" + _tag("image")):
                href = node.attrib.get("href", node.attrib.get(f"{{{XLINK_NS}}}href", ""))
                if not href.startswith("data:image/png;base64,"):
                    messages.append(f"invalid embedded raster href on {node.attrib.get('id', '?')}")
                else:
                    try:
                        base64.b64decode(href.split(",", 1)[1], validate=True)
                    except ValueError:
                        messages.append(f"invalid base64 raster on {node.attrib.get('id', '?')}")
            for node in nodes:
                for field in ("width", "height", "stroke-width"):
                    if field in node.attrib:
                        try:
                            if float(node.attrib[field]) < 0:
                                messages.append(f"negative {field} on {node.attrib.get('id', '?')}")
                        except ValueError:
                            messages.append(f"invalid numeric {field} on {node.attrib.get('id', '?')}")
            if root.attrib.get("data-adapter-config-hash") != result.adapter_config_hash:
                messages.append("adapter config hash mismatch")
        except (OSError, ET.ParseError) as exc:
            messages.append(f"invalid SVG: {exc}")
        return AdapterValidationResult(not messages, tuple(messages))

    def collect_runtime_evidence(self, result: AdapterRealizationResult) -> tuple[EvidenceRecord, ...]:
        return (
            EvidenceRecord(
                evidence_id="evidence-svg-renderer-phase5b",
                source_type=EvidenceSourceType.RENDERER,
                source_hash=result.artifact_hashes[0][1],
                producer=SVG_ADAPTER_VERSION,
                operation="realize-and-validate-structured-svg",
                input_refs=tuple(item.canonical_object_id for item in result.realizations),
                output_refs=result.artifact_refs,
                run_index=0,
                tool_runtime_info={
                    "adapter_config_hash": result.adapter_config_hash,
                    "runtime": "python-stdlib-elementtree",
                    "external_calls": "0",
                },
                notes="XML, identity mappings, semantic relations, assets, and geometry verified.",
            ),
        )

    @staticmethod
    def _append_label_container(parent, container):
        # Independently authored for release review; see SVG_CLOSURE.md.
        box = container["bbox"]
        left, top = box["x"], box["y"]
        width, height = box["width"], box["height"]
        kind = container["preset"]
        properties = {}
        for key in ("fill", "stroke"):
            properties[key] = container.get(key) or "none"
        properties["stroke-width"] = str(container.get("stroke_width_px", 0))
        properties["data-label-container"] = kind

        if kind == "ellipse":
            dimensions = (left + width / 2, top + height / 2, width / 2, height / 2)
            properties.update(zip(("cx", "cy", "rx", "ry"), map(str, dimensions)))
            element = "ellipse"
        elif kind == "can":
            radius = min(height / 4, width / 6)
            right = left + width
            upper, lower = top + radius, top + height - radius
            commands = [
                ("M", left, upper),
                ("A", width / 2, radius, 0, 0, 1, right, upper),
                ("L", right, lower),
                ("A", width / 2, radius, 0, 0, 1, left, lower),
                ("Z",),
                ("M", left, upper),
                ("A", width / 2, radius, 0, 0, 0, right, upper),
            ]
            properties["d"] = " ".join(str(token) for command in commands for token in command)
            element = "path"
        else:
            properties.update(zip(("x", "y", "width", "height"), map(str, (left, top, width, height))))
            if kind == "roundRect":
                adjustment = 16667
                for declaration in container.get("adjustments", []):
                    if declaration["name"] == "adj":
                        adjustment = int(declaration["fmla"].split()[1])
                        break
                properties["rx"] = str(min(width, height) * min(adjustment, 50000) / 100000)
            element = "rect"
        ET.SubElement(parent, _tag(element), properties)

    @staticmethod
    def _append_shape(parent: ET.Element, component: Component, attrs: dict[str, str]) -> None:
        bbox = component.geometry.bbox
        role = component.semantic_role.lower().replace("-", "_")
        common = {
            **attrs,
            "fill": svg_color(component.style.fill, "FFFFFF"),
            "stroke": svg_color(component.style.stroke, "000000"),
            "stroke-width": str(component.style.stroke_width),
            "opacity": str(component.style.opacity),
        }
        if role in {"ellipse", "circle"}:
            common.update(
                {
                    "cx": str(bbox.x + bbox.width / 2),
                    "cy": str(bbox.y + bbox.height / 2),
                    "rx": str(bbox.width / 2),
                    "ry": str(bbox.height / 2),
                }
            )
            ET.SubElement(parent, _tag("ellipse"), common)
        elif role == "line":
            common.update(
                {
                    "x1": str(bbox.x),
                    "y1": str(bbox.y),
                    "x2": str(bbox.x + bbox.width),
                    "y2": str(bbox.y + bbox.height),
                }
            )
            ET.SubElement(parent, _tag("line"), common)
        else:
            common.update(
                {
                    "x": str(bbox.x),
                    "y": str(bbox.y),
                    "width": str(bbox.width),
                    "height": str(bbox.height),
                }
            )
            if role in {"rounded_rectangle", "roundrect"}:
                common["rx"] = str(min(bbox.width, bbox.height) * 0.1)
                common["ry"] = common["rx"]
            ET.SubElement(parent, _tag("rect"), common)

    @staticmethod
    def _namespace_source_ids(source: ET.Element, prefix: str) -> None:
        mapping: dict[str, str] = {}
        for node in source.iter():
            old = node.attrib.get("id")
            if old:
                mapping[old] = f"{prefix}--source--{_safe_id('asset-node', old)}"
        for node in source.iter():
            old = node.attrib.get("id")
            if old:
                node.set("id", mapping[old])
            for key, value in tuple(node.attrib.items()):
                for source_id, target_id in mapping.items():
                    value = value.replace(f"url(#{source_id})", f"url(#{target_id})")
                    if value == f"#{source_id}":
                        value = f"#{target_id}"
                node.set(key, value)
