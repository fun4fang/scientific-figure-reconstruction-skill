from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json


ANALYSIS_COST_POLICY_VERSION_V0_2 = "analysis-cost-policy-v0.2"


@dataclass(frozen=True, slots=True)
class AnalysisCostBudgetV0_2:
    full_image_call_limit: int = 1
    roi_call_limit: int = 1
    total_analysis_call_limit: int = 2
    soft_input_token_budget: int | None = None
    soft_output_token_budget: int | None = None

    def __post_init__(self) -> None:
        if self.full_image_call_limit < 0 or self.roi_call_limit < 0:
            raise ValueError("call budgets must be nonnegative")
        if self.total_analysis_call_limit < self.full_image_call_limit:
            raise ValueError("total call budget cannot be below full-image budget")
        for value in (self.soft_input_token_budget, self.soft_output_token_budget):
            if value is not None and value < 0:
                raise ValueError("soft token budgets must be nonnegative")

    def policy_hash(self) -> str:
        payload = {
            "version": ANALYSIS_COST_POLICY_VERSION_V0_2,
            **asdict(self),
        }
        return "sha256:" + sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AnalysisBudgetObservationV0_2:
    full_image_calls: int
    roi_calls: int
    input_tokens: int | str
    output_tokens: int | str


@dataclass(frozen=True, slots=True)
class AnalysisBudgetEvaluationV0_2:
    hard_limit_passed: bool
    warnings: tuple[str, ...]
    evidence_codes: tuple[str, ...]


class AnalysisBudgetEvaluatorV0_2:
    def evaluate(
        self,
        observation: AnalysisBudgetObservationV0_2,
        budget: AnalysisCostBudgetV0_2,
    ) -> AnalysisBudgetEvaluationV0_2:
        errors: list[str] = []
        warnings: list[str] = []
        if observation.full_image_calls > budget.full_image_call_limit:
            errors.append("FULL_IMAGE_CALL_BUDGET_EXCEEDED")
        if observation.roi_calls > budget.roi_call_limit:
            errors.append("ROI_CALL_BUDGET_EXCEEDED")
        if observation.full_image_calls + observation.roi_calls > budget.total_analysis_call_limit:
            errors.append("TOTAL_ANALYSIS_CALL_BUDGET_EXCEEDED")
        if (
            budget.soft_input_token_budget is not None
            and isinstance(observation.input_tokens, int)
            and observation.input_tokens > budget.soft_input_token_budget
        ):
            warnings.append("SOFT_INPUT_TOKEN_BUDGET_EXCEEDED")
        if (
            budget.soft_output_token_budget is not None
            and isinstance(observation.output_tokens, int)
            and observation.output_tokens > budget.soft_output_token_budget
        ):
            warnings.append("SOFT_OUTPUT_TOKEN_BUDGET_EXCEEDED")
        return AnalysisBudgetEvaluationV0_2(
            hard_limit_passed=not errors,
            warnings=tuple(warnings),
            evidence_codes=tuple((*errors, *warnings)),
        )


DEFAULT_ANALYSIS_COST_BUDGET_V0_2 = AnalysisCostBudgetV0_2()
