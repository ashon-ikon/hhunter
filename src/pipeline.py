"""Run the full snapshot pipeline in one command."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.analyze_spreads import run_analysis
from src.extract_har import extract_snapshot
from src.normalize_har import find_snapshot, normalize_snapshot
from src.qa import run_qa


def main() -> None:
    parser = argparse.ArgumentParser(description="Run extract->normalize->qa->analyze pipeline")
    parser.add_argument("--snapshot", help="Snapshot pack path (defaults to latest)")
    parser.add_argument("--inputs", nargs="*", type=Path, help="HAR file(s) or folder(s) to extract")
    parser.add_argument("--force", action="store_true", help="Run analyze even when QA fails")
    parser.add_argument("--min-cohort", type=int, default=10, help="Minimum cohort size")
    parser.add_argument("--max-missing-sqft-pct", type=float, default=35.0)
    parser.add_argument("--max-missing-year-built-pct", type=float, default=50.0)
    parser.add_argument("--max-missing-price-pct", type=float, default=35.0)
    parser.add_argument("--max-rental-contamination-count", type=int, default=0)
    args = parser.parse_args()

    snapshot = find_snapshot(args.snapshot)
    inputs = args.inputs or [snapshot / "raw" / "har"]

    print(f"Pipeline snapshot: {snapshot}")
    print("Step 1/4: extract-har")
    extract_result = extract_snapshot(inputs=inputs, snapshot_path=snapshot)
    print(
        f"  extracted payloads={extract_result['extracted_payloads']} listings={extract_result['merged_listings']}"
    )

    print("Step 2/4: normalize")
    norm_result = normalize_snapshot(snapshot)
    print(f"  normalized rows={norm_result['normalized_rows']}")

    print("Step 3/4: qa")
    qa_result = run_qa(
        snapshot=snapshot,
        max_missing_sqft_pct=args.max_missing_sqft_pct,
        max_missing_year_built_pct=args.max_missing_year_built_pct,
        max_missing_price_pct=args.max_missing_price_pct,
        max_rental_contamination_count=args.max_rental_contamination_count,
    )
    print(f"  qa status={'PASS' if qa_result['passed'] else 'FAIL'}")
    if qa_result["failures"]:
        for failure in qa_result["failures"]:
            print(f"  - {failure}")

    if not qa_result["passed"] and not args.force:
        raise SystemExit("QA failed. Re-run with --force to continue to analysis.")

    print("Step 4/4: analyze")
    analysis_result = run_analysis(snapshot=snapshot, force=args.force, min_cohort=args.min_cohort)
    print(
        "  outputs: "
        f"ranked={analysis_result['ranked_path']}, "
        f"scoreboard={analysis_result['scoreboard_path']}, "
        f"streets={analysis_result['streets_path']}"
    )


if __name__ == "__main__":
    main()
