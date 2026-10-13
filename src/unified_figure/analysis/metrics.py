from __future__ import annotations

from collections import Counter
import re

from unified_figure.model.figure import CanonicalFigure

from .proposal import AnalysisProposal


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def evaluate_structural_metrics(
    ground_truth: dict[str, object],
    proposal: AnalysisProposal,
    figure: CanonicalFigure,
) -> dict[str, object]:
    expected_counts = Counter(ground_truth.get("components", {}))
    actual_counts = Counter(item.component_type.value for item in figure.components)
    component_type_recovery = {
        key: {
            "expected": expected_counts[key],
            "actual": actual_counts[key],
            "recovered_at_least_expected": actual_counts[key] >= expected_counts[key],
        }
        for key in sorted(set(expected_counts) | set(actual_counts))
    }

    expected_texts = [str(item) for item in ground_truth.get("texts", [])]
    observed_texts = [
        item.text_content or ""
        for item in proposal.component_proposals
        if item.proposed_type == "TEXT"
    ]
    exact_matches = sum(text in observed_texts for text in expected_texts)
    normalized_observed = {_normalize_text(item) for item in observed_texts}
    normalized_matches = sum(_normalize_text(text) in normalized_observed for text in expected_texts)
    concatenated_expected = " ".join(_normalize_text(item) for item in expected_texts)
    concatenated_observed = " ".join(_normalize_text(item) for item in observed_texts)
    cer = (
        _edit_distance(concatenated_expected, concatenated_observed)
        / max(1, len(concatenated_expected))
    )

    expected_relations = list(ground_truth.get("relations", []))
    relation_metrics = {
        "expected_count": len(expected_relations),
        "proposal_detected_count": len(proposal.relation_proposals),
        "canonicalized_count": len(figure.relations),
        "source_resolved_count": sum(
            item.source_candidate_id is not None for item in proposal.relation_proposals
        ),
        "target_resolved_count": sum(
            item.target_candidate_id is not None for item in proposal.relation_proposals
        ),
        "direction_known_count": sum(
            item.direction != "UNKNOWN" for item in proposal.relation_proposals
        ),
        "type_known_count": sum(
            item.relation_type != "UNKNOWN" for item in proposal.relation_proposals
        ),
        "route_known_count": sum(bool(item.route_type) for item in proposal.relation_proposals),
    }

    expected_coverage = dict(ground_truth.get("coverage", {}))
    canonical_coverage = {item.dimension.value: item.status.value for item in figure.coverage}
    coverage_metrics = {
        key: {
            "expected": expected_coverage[key],
            "canonical": canonical_coverage.get(key, "ABSENT"),
            "match": canonical_coverage.get(key) == expected_coverage[key],
        }
        for key in sorted(expected_coverage)
    }
    review_targets = Counter(item.reason_type.value for item in figure.review_items)
    return {
        "component_type_recovery": component_type_recovery,
        "text_recovery": {
            "expected_count": len(expected_texts),
            "observed_count": len(observed_texts),
            "exact_match_count": exact_matches,
            "normalized_match_count": normalized_matches,
            "character_error_rate": round(cer, 6),
        },
        "relation_recovery": relation_metrics,
        "group_recovery": {
            "expected_count": len(ground_truth.get("groups", [])),
            "proposal_count": len(proposal.group_proposals),
            "canonical_count": len(figure.groups),
        },
        "coverage_correctness": coverage_metrics,
        "uncertainty_calibration_observations": {
            "typed_uncertainties": len(proposal.uncertainties),
            "roi_resolvable": sum(item.resolvable_by_roi for item in proposal.uncertainties),
        },
        "representation_candidate_correctness": {
            "expected_ranges": ground_truth.get("representation_ranges", {}),
            "canonical_by_type": {
                item.component_type.value: item.representation.value for item in figure.components
            },
        },
        "review_quality": {
            "review_count": len(figure.review_items),
            "localized_count": sum(item.target_id != figure.figure_id for item in figure.review_items),
            "typed_reason_counts": dict(sorted(review_targets.items())),
            "candidate_action_count": sum(bool(item.candidate_actions) for item in figure.review_items),
        },
    }
