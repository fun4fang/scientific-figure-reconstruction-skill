from __future__ import annotations

from collections import Counter, defaultdict

from unified_figure.model.enums import (
    AssetSourceType,
    AssetType,
    CheckStatus,
    CoverageDimension,
    CoverageStatus,
    DecisionStatus,
    FailureReason,
    FailureState,
    ReconstructionStatus,
    RepresentationType,
    ReviewStatus,
)
from unified_figure.model.figure import CanonicalFigure, SCHEMA_VERSION
from unified_figure.model.geometry import ModelValidationError


def _raise_if_errors(errors: list[str]) -> None:
    if errors:
        raise ModelValidationError("canonical validation failed: " + "; ".join(errors))


def _capture(errors: list[str], label: str, operation) -> None:
    try:
        operation()
    except ModelValidationError as exc:
        errors.append(f"{label}: {exc}")


class CanonicalStructureValidator:
    def validate(self, figure: CanonicalFigure) -> None:
        errors: list[str] = []
        if figure.schema_version != SCHEMA_VERSION:
            errors.append(f"schema_version must be {SCHEMA_VERSION}")
        _capture(errors, "input_geometry", figure.input_geometry.validate)
        _capture(errors, "revision", figure.revision.validate)
        _capture(errors, "cost_telemetry", figure.cost_telemetry.validate)
        if figure.revision.canonical_state_hash:
            expected_hash = figure.state_hash()
            if figure.revision.canonical_state_hash != expected_hash:
                errors.append("revision canonical_state_hash does not match canonical state")

        collections = (
            ("component", figure.components, "component_id"),
            ("group", figure.groups, "group_id"),
            ("relation", figure.relations, "relation_id"),
            ("asset", figure.assets, "asset_id"),
            ("decision", figure.representation_decisions, "decision_id"),
            ("fallback", figure.fallbacks, "fallback_id"),
            ("coverage", figure.coverage, "coverage_id"),
            ("review", figure.review_items, "review_id"),
            ("failure", figure.failures, "failure_id"),
            ("evidence", figure.evidence, "evidence_id"),
            ("realization", figure.renderer_realizations, "realization_id"),
        )
        all_ids = [figure.figure_id, figure.revision.revision_id]
        for label, values, id_field in collections:
            ids = [getattr(value, id_field) for value in values]
            duplicates = [key for key, count in Counter(ids).items() if count > 1]
            if duplicates:
                errors.append(f"duplicate {label} IDs: {duplicates}")
            all_ids.extend(ids)
            for value in values:
                _capture(errors, f"{label} {getattr(value, id_field)}", value.validate)
        global_duplicates = [key for key, count in Counter(all_ids).items() if count > 1]
        if global_duplicates:
            errors.append(f"canonical IDs are not globally unique: {global_duplicates}")

        component_ids = {item.component_id for item in figure.components}
        group_ids = {item.group_id for item in figure.groups}
        asset_ids = {item.asset_id for item in figure.assets}
        decision_by_id = {item.decision_id: item for item in figure.representation_decisions}
        evidence_ids = {item.evidence_id for item in figure.evidence}
        realization_ids = {item.realization_id for item in figure.renderer_realizations}
        canonical_object_ids = (
            component_ids
            | group_ids
            | {item.relation_id for item in figure.relations}
            | asset_ids
            | {figure.figure_id}
        )

        for component in figure.components:
            if component.parent_component_id and component.parent_component_id not in component_ids:
                errors.append(f"component {component.component_id} has dangling parent")
            if component.asset_ref and component.asset_ref not in asset_ids:
                errors.append(f"component {component.component_id} has dangling asset_ref")
            decision = decision_by_id.get(component.representation_decision_id)
            if decision is None:
                errors.append(f"component {component.component_id} has dangling decision")
            elif decision.component_id != component.component_id:
                errors.append(f"component {component.component_id} decision targets another component")
            elif decision.selected_representation != component.representation:
                errors.append(f"component {component.component_id} representation disagrees with decision")
            for realization_id in component.renderer_realizations:
                if realization_id not in realization_ids:
                    errors.append(f"component {component.component_id} has dangling realization")
            for evidence_id in component.provenance.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"component {component.component_id} has dangling provenance evidence")

        for group in figure.groups:
            if group.parent_group_id and group.parent_group_id not in group_ids:
                errors.append(f"group {group.group_id} has dangling parent group")
            missing = set(group.member_component_ids) - component_ids
            if missing:
                errors.append(f"group {group.group_id} has dangling members: {sorted(missing)}")
            for evidence_id in group.provenance.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"group {group.group_id} has dangling provenance evidence")

        for asset in figure.assets:
            for evidence_id in asset.provenance.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"asset {asset.asset_id} has dangling provenance evidence")

        for relation in figure.relations:
            if relation.source_component_id not in component_ids:
                errors.append(f"relation {relation.relation_id} has dangling source")
            if relation.target_component_id not in component_ids:
                errors.append(f"relation {relation.relation_id} has dangling target")
            for realization_id in relation.renderer_realizations:
                if realization_id not in realization_ids:
                    errors.append(f"relation {relation.relation_id} has dangling realization")
            for evidence_id in relation.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"relation {relation.relation_id} has dangling evidence")

        review_targets = canonical_object_ids | {
            item.decision_id for item in figure.representation_decisions
        } | {item.coverage_id for item in figure.coverage} | realization_ids
        for review in figure.review_items:
            if review.target_id not in review_targets:
                errors.append(f"review {review.review_id} has dangling target")
            for evidence_id in review.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"review {review.review_id} has dangling evidence")

        for coverage in figure.coverage:
            missing = set(coverage.target_ids) - canonical_object_ids
            if missing:
                errors.append(f"coverage {coverage.coverage_id} has dangling targets: {sorted(missing)}")
            for evidence_id in coverage.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"coverage {coverage.coverage_id} has dangling evidence")

        for realization in figure.renderer_realizations:
            refs = {realization.canonical_object_id, *realization.contributing_canonical_object_ids}
            missing = refs - canonical_object_ids
            if missing:
                errors.append(
                    f"realization {realization.realization_id} has dangling canonical refs: {sorted(missing)}"
                )
            component = next(
                (item for item in figure.components if item.component_id == realization.canonical_object_id),
                None,
            )
            relation = next(
                (item for item in figure.relations if item.relation_id == realization.canonical_object_id),
                None,
            )
            canonical_representation = (
                component.representation
                if component is not None
                else relation.representation
                if relation is not None
                else None
            )
            if (
                canonical_representation is not None
                and realization.representation_used is not canonical_representation
            ):
                errors.append(
                    f"realization {realization.realization_id} representation disagrees with canonical object"
                )
            if realization.artifact_ref is not None:
                normalized = realization.artifact_ref.replace("\\", "/")
                if normalized.startswith("/") or ":/" in normalized:
                    errors.append(
                        f"realization {realization.realization_id} artifact_ref is an absolute machine path"
                    )

        for decision in figure.representation_decisions:
            if decision.component_id not in component_ids:
                errors.append(f"decision {decision.decision_id} has dangling component")
            for evidence_id in decision.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"decision {decision.decision_id} has dangling evidence")

        for evidence_id in figure.revision.evidence_refs:
            if evidence_id not in evidence_ids:
                errors.append("revision has dangling evidence")

        fallback_edges: dict[str, str] = {}
        for fallback in figure.fallbacks:
            fallback_edges[fallback.original_decision_id] = fallback.fallback_decision_id
            for evidence_id in (
                *fallback.backend_capability_evidence_refs,
                *fallback.provenance.evidence_refs,
            ):
                if evidence_id not in evidence_ids:
                    errors.append(f"fallback {fallback.fallback_id} has dangling evidence")
        for start in fallback_edges:
            seen: set[str] = set()
            current = start
            while current in fallback_edges:
                if current in seen:
                    errors.append(f"fallback chain contains a cycle at {current}")
                    break
                seen.add(current)
                current = fallback_edges[current]

        for failure in figure.failures:
            if failure.target_id not in review_targets:
                errors.append(f"failure {failure.failure_id} has dangling target")
            for evidence_id in failure.evidence_refs:
                if evidence_id not in evidence_ids:
                    errors.append(f"failure {failure.failure_id} has dangling evidence")

        _raise_if_errors(errors)


class CoverageValidator:
    def validate(self, figure: CanonicalFigure) -> None:
        errors: list[str] = []
        present_dimensions = {item.dimension for item in figure.coverage}
        missing_dimensions = set(CoverageDimension) - present_dimensions
        if missing_dimensions:
            errors.append(
                "missing required coverage dimensions: "
                + ", ".join(sorted(item.value for item in missing_dimensions))
            )
        blocking = [
            item
            for item in figure.coverage
            if item.mandatory
            and item.status in {CoverageStatus.MISSING, CoverageStatus.ALTERED, CoverageStatus.UNKNOWN}
        ]
        if blocking and figure.reconstruction_status is ReconstructionStatus.SUCCESS:
            errors.append("mandatory missing/altered/unknown coverage cannot declare SUCCESS")
        active_failures = [
            item
            for item in figure.failures
            if item.state in {FailureState.ACTIVE, FailureState.REVIEW_REQUIRED, FailureState.TERMINAL}
        ]
        if active_failures and figure.reconstruction_status is ReconstructionStatus.SUCCESS:
            errors.append("active fail-closed records cannot declare SUCCESS")
        _raise_if_errors(errors)


class RepresentationValidator:
    def validate(self, figure: CanonicalFigure) -> None:
        errors: list[str] = []
        assets = {item.asset_id: item for item in figure.assets}
        reviews_by_target = defaultdict(list)
        for item in figure.review_items:
            reviews_by_target[item.target_id].append(item)
        failures_by_target = defaultdict(list)
        for item in figure.failures:
            failures_by_target[item.target_id].append(item)

        for component in figure.components:
            representation = component.representation
            asset = assets.get(component.asset_ref) if component.asset_ref else None
            if representation is RepresentationType.REVIEW_REQUIRED and not reviews_by_target[component.component_id]:
                errors.append(f"REVIEW_REQUIRED component {component.component_id} has no ReviewItem")
            if representation is RepresentationType.UNSUPPORTED:
                decision = next(
                    (
                        item
                        for item in figure.representation_decisions
                        if item.decision_id == component.representation_decision_id
                    ),
                    None,
                )
                if decision is None or not decision.decision_reason.strip():
                    errors.append(f"UNSUPPORTED component {component.component_id} lacks explicit reason")
            if representation in {
                RepresentationType.EXACT_RASTER_ASSET,
                RepresentationType.GENERATED_COMPLEX_ASSET,
                RepresentationType.RASTER_PRESERVE_COMPLEX,
                RepresentationType.VECTOR_ASSET,
            } and asset is None:
                errors.append(f"asset representation on {component.component_id} lacks asset_ref")
                continue
            if representation is RepresentationType.EXACT_RASTER_ASSET:
                if asset.source_type is not AssetSourceType.SOURCE_EXACT:
                    errors.append(f"exact asset {asset.asset_id} is not SOURCE_EXACT")
                if not asset.content_hash or not asset.provenance.evidence_refs:
                    errors.append(f"exact asset {asset.asset_id} lacks source provenance")
            if representation is RepresentationType.GENERATED_COMPLEX_ASSET:
                if asset.source_type is not AssetSourceType.SOURCE_GENERATED:
                    errors.append(f"generated asset {asset.asset_id} is not SOURCE_GENERATED")
                if asset.scientific_review is None or not asset.content_hash:
                    errors.append(f"generated asset {asset.asset_id} lacks generation provenance/review")
                if (
                    asset.scientific_review is not None
                    and asset.scientific_review.review_status is ReviewStatus.NOT_REQUIRED
                ):
                    errors.append(f"generated asset {asset.asset_id} must require scientific review")
                if not reviews_by_target[component.component_id]:
                    errors.append(f"generated asset component {component.component_id} has no ReviewItem")
                elif (
                    asset.scientific_review is not None
                    and asset.scientific_review.has_deviation()
                    and not any(
                        failure.reason is FailureReason.SCIENTIFIC_GENERATION_DEVIATION
                        and failure.state in {FailureState.ACTIVE, FailureState.REVIEW_REQUIRED, FailureState.TERMINAL}
                        for failure in failures_by_target[component.component_id]
                    )
                ):
                    errors.append(f"generated deviation on {component.component_id} did not fail closed")
            if representation is RepresentationType.VECTOR_ASSET:
                if asset.asset_type is not AssetType.VECTOR:
                    errors.append(f"vector asset {asset.asset_id} has non-vector asset_type")
                if asset.source_type is not AssetSourceType.SOURCE_VECTOR or not asset.content_hash:
                    errors.append(f"vector asset {asset.asset_id} lacks vector provenance")

        for relation in figure.relations:
            if relation.representation is not RepresentationType.NATIVE_RELATION and relation.representation not in {
                RepresentationType.VECTOR_ASSET,
                RepresentationType.EXACT_RASTER_ASSET,
                RepresentationType.REVIEW_REQUIRED,
                RepresentationType.UNSUPPORTED,
            }:
                errors.append(f"relation {relation.relation_id} has invalid relation representation")
            if (
                relation.representation is RepresentationType.REVIEW_REQUIRED
                and not reviews_by_target[relation.relation_id]
            ):
                errors.append(f"REVIEW_REQUIRED relation {relation.relation_id} has no ReviewItem")

        decisions = {item.decision_id: item for item in figure.representation_decisions}
        fallbacks_by_decision = {item.fallback_decision_id: item for item in figure.fallbacks}
        decisions_by_component = defaultdict(list)
        for decision in figure.representation_decisions:
            decisions_by_component[decision.component_id].append(decision)
            if decision.status is DecisionStatus.FALLBACK_SELECTED and decision.decision_id not in fallbacks_by_decision:
                errors.append(f"fallback decision {decision.decision_id} lacks ExplicitFallbackRecord")
        for component_id, component_decisions in decisions_by_component.items():
            if len(component_decisions) > 1:
                selected = [
                    item
                    for item in component_decisions
                    if item.status in {DecisionStatus.SELECTED, DecisionStatus.FALLBACK_SELECTED}
                ]
                if selected and any(item.status is DecisionStatus.SUPERSEDED for item in component_decisions):
                    if not any(item.component_id == component_id for item in figure.fallbacks):
                        errors.append(f"component {component_id} has a silent fallback")

        for fallback in figure.fallbacks:
            original = decisions.get(fallback.original_decision_id)
            target = decisions.get(fallback.fallback_decision_id)
            if original is None or target is None:
                errors.append(f"fallback {fallback.fallback_id} has dangling decision")
                continue
            if original.component_id != fallback.component_id or target.component_id != fallback.component_id:
                errors.append(f"fallback {fallback.fallback_id} crosses components")
            if target.selected_representation is not fallback.fallback_target:
                errors.append(f"fallback {fallback.fallback_id} target disagrees with decision")

        _raise_if_errors(errors)


def validate_canonical_figure(figure: CanonicalFigure) -> None:
    CanonicalStructureValidator().validate(figure)
    CoverageValidator().validate(figure)
    RepresentationValidator().validate(figure)
    # Opt-in attachment evidence must survive canonical serialization and
    # routing, not merely an individual Writer's validation. Legacy states
    # without this metadata keep their existing behavior.
    from unified_figure.model.semantic_attachment import KEY, get_attachment
    for relation in figure.relations:
        if KEY + relation.relation_id in figure.metadata or figure.metadata.get('visual.reauthoring.version'):
            try:
                get_attachment(figure, relation)
            except (ValueError, KeyError, TypeError) as exc:
                raise ModelValidationError("canonical semantic attachment rejected: " + str(exc)) from exc
