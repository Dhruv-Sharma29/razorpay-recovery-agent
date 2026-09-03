"""CLI entry point for the synthetic dataset generator.

Usage:
    python -m app.ingestion.generator [--seed SEED] [--total TOTAL]
"""

from __future__ import annotations

import argparse
import json
import sys

from app.ingestion.generator import DEFAULT_SEED, TOTAL_EVENTS, generate_and_write


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic failed-payment dataset"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for reproducibility (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--total",
        type=int,
        default=TOTAL_EVENTS,
        help=f"Total number of events to generate, 50-100 (default: {TOTAL_EVENTS})",
    )
    args = parser.parse_args()

    try:
        stats = generate_and_write(seed=args.seed, total=args.total)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(stats, indent=2))
    print(f"\nGenerated {stats['total_events']} events")
    print(f"   Development: {stats['dev_count']}  →  {stats['synthetic_path']}")
    print(f"   Held-out:    {stats['held_out_count']}  →  {stats['held_out_path']}")


if __name__ == "__main__":
    main()
