#!/usr/bin/env python3
"""Run evaluation against synthetic and held-out data (TASK-010)."""

import json
import argparse
from pathlib import Path
from unittest.mock import patch

from app.evaluation.harness import Evaluator, EvaluationReport
from app.reasoning.engine import RecoveryReasoner
from app.recommendation.engine import RecoveryRecommender


def _rupees(paise: int) -> str:
    """Paise are the wire unit; rupees are what a human reads."""
    return f"Rs {paise / 100:,.2f}"


def print_report(report: EvaluationReport) -> None:
    """Print a human-readable summary of the evaluation report."""
    print(f"\n{'='*50}")
    print(f"EVALUATION REPORT: {report.dataset_name}")
    print(f"{'='*50}")
    print(f"Total Transactions:        {report.total_transactions}")
    print(f"Classification Accuracy:   {report.classification_accuracy:.2%}")
    print(f"Automatic Recoveries:      {report.automatic_recovery_count}")
    print(f"Escalations:               {report.escalation_count}")
    print(f"Denials:                   {report.denial_count}")
    print(f"Execution Failures:        {report.execution_failure_count}")
    print(f"Unknown/Unsafe count:      {report.unknown_unsafe_count}")
    print(f"False Auto-Recoveries:     {report.false_automatic_recovery_count}")
    print(f"Risk Precision / Recall:   {report.risk_detection_precision if report.risk_detection_precision is not None else 'n/a'} / {report.risk_detection_recall if report.risk_detection_recall is not None else 'n/a'}")
    print(f"AI Recommendations:        model={report.recommendation_model_generated_count}, fallback={report.recommendation_fallback_count}")
    print(f"Recommendation Treatment:   {report.recommendation_status_counts or 'none'}")
    print(f"Policy Isolation:           {'PASS' if report.policy_isolation_passed else 'FAIL'} ({report.policy_isolation_violation_count} violations)")
    print(f"False Escalation Cost:      {_rupees(report.false_escalation_cost)} ({report.false_escalation_count} cases)")

    print(f"\n{'-'*50}")
    print("MONEY RECOVERED (simulated executor, test mode)")
    print(f"{'-'*50}")
    print(f"Amount Attempted:          {_rupees(report.total_attempted_amount)}")
    print(f"Amount Recovered:          {_rupees(report.total_recovered_amount)}")
    print(f"Amount Escalated:          {_rupees(report.amount_escalated)}")
    print(f"Amount Not Recovered:      {_rupees(report.amount_failed)}")
    print(f"Amount Recoverable:        {_rupees(report.total_recoverable_amount)}"
          "   (what policy authorised chasing)")
    print(f"Recovery Rate (of recoverable): {report.recovery_rate_of_recoverable:.2%}"
          "   <- how well the agent does its job")
    print(f"Recovery Rate (by amount): {report.recovery_rate_by_amount:.2%}"
          "   (includes what policy correctly refused)")
    print(f"Recovery Rate (by count):  {report.recovery_rate_by_count:.2%}")

    if report.by_category:
        print("\nBy category:")
        for name, b in sorted(report.by_category.items()):
            print(
                f"  {name:<24} {_rupees(b.recovered_amount):>14} of "
                f"{_rupees(b.attempted_amount):<14} "
                f"({b.recovery_rate_amount:.0%})  n={b.count}"
            )

    if report.false_automatic_recovery_count > 0:
        print("\nWARNING: False automatic recoveries detected!")
        for rec in report.records:
            if rec.is_false_automatic_recovery:
                print(f"  - {rec.event_id} (Expected: {rec.expected_failure_category}, Allowed: {rec.automatic_recovery_allowed})")
    print(f"{'='*50}\n")


def mock_reasoning_analyze(self, event, classification, policy_decision):
    """Mock reasoning to avoid real NIM API calls during large evaluations."""
    from app.reasoning.result import ReasoningResult
    return ReasoningResult(
        success=True,
        recommendation="Mocked recommendation",
        explanation="Mocked explanation for evaluation",
        confidence=0.9,
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        policy_action_allowed=policy_decision.automatic_recovery_allowed if policy_decision else False,
        is_fallback=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Evaluate synthetic and held-out recovery data")
    parser.add_argument(
        "--with-ai",
        action="store_true",
        help="Call configured NVIDIA NIM for recommendations; otherwise use safe fallbacks",
    )
    args = parser.parse_args()
    root_dir = Path(__file__).parent.parent
    synthetic_path = root_dir / "data" / "synthetic" / "failed_transactions.json"
    held_out_path = root_dir / "data" / "held_out" / "failed_transactions.json"
    output_dir = Path(__file__).parent / "evaluation_results"
    output_dir.mkdir(exist_ok=True)

    # Patch the reasoning engine so we don't spam the NIM API
    with patch.object(RecoveryReasoner, "analyze", mock_reasoning_analyze):
        evaluator = Evaluator(
            recommender=RecoveryRecommender() if args.with_ai else None
        )
        
        # 1. Synthetic Data
        print("Evaluating synthetic dataset...")
        synthetic_report = evaluator.evaluate("Synthetic", synthetic_path)
        print_report(synthetic_report)
        
        with open(output_dir / "synthetic_report.json", "w") as f:
            f.write(synthetic_report.model_dump_json(indent=2))
            
        # 2. Held-Out Data
        print("Evaluating held-out dataset...")
        held_out_report = evaluator.evaluate("Held-Out", held_out_path)
        print_report(held_out_report)
        
        with open(output_dir / "held_out_report.json", "w") as f:
            f.write(held_out_report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
