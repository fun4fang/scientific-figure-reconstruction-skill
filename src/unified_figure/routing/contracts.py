from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from unified_figure.adapters.contracts import BackendCapabilityProfile
from unified_figure.model.enums import CostClass
from unified_figure.model.records import Component, RepresentationDecision


@dataclass(frozen=True, slots=True)
class SemanticContext:
    figure_id: str
    group_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    scientific_criticality: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EditingRequirements:
    required_operations: tuple[str, ...]
    minimum_editability: str
    fidelity_priority: str


@dataclass(frozen=True, slots=True)
class CostPolicy:
    maximum_execution_cost: CostClass
    maximum_review_cost: CostClass
    optional_backends_allowed: bool


class RepresentationRouter(ABC):
    @abstractmethod
    def decide(
        self,
        component: Component,
        semantic_context: SemanticContext,
        editing_requirements: EditingRequirements,
        backend_capabilities: tuple[BackendCapabilityProfile, ...],
        cost_policy: CostPolicy,
    ) -> RepresentationDecision:
        raise NotImplementedError
