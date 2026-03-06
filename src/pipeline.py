"""Run the full snapshot pipeline in one command."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.analyze_spreads import parse_zip_whitelist, run_analysis
from src.extract_har import extract_snapshot
from src.grid_analysis import run_grid_analysis
from src.normalize_har import find_snapshot, normalize_snapshot
from src.qa import run_qa


def main() -> None:
    parser = argparse.ArgumentParser(description="Run extract->normalize->qa->analyze->grid pipeline")
    parser.add_argument("--snapshot", help="Snapshot pack path (defaults to latest)")
    parser.add_argument("--inputs", nargs="*", type=Path, help="HAR file(s) or folder(s) to extract")
    parser.add_argument(
        "--replay-failures",
        action="store_true",
        help="Attempt replay for failed SearchListings JSON parses during extraction",
    )
    parser.add_argument("--replay-timeout", type=int, default=25, help="HTTP timeout for replay requests")
    parser.add_argument("--force", action="store_true", help="Run analyze even when QA fails")
    parser.add_argument("--min-cohort", type=int, default=10, help="Minimum cohort size")
    parser.add_argument(
        "--include-non-flip-box",
        action="store_true",
        help="Do not require flip_box_flag for ranked candidates",
    )
    parser.add_argument("--max-list-price", type=float, help="Only rank actives at or below this list price")
    parser.add_argument(
        "--min-upside-to-p70",
        type=float,
        help="Only rank candidates with upside to cohort p70 at or above this threshold",
    )
    parser.add_argument(
        "--zip-whitelist",
        help="Comma-separated ZIP whitelist for ranked, scoreboard, and street outputs",
    )
    parser.add_argument("--skip-grid-analysis", action="store_true", help="Skip grid-based scouting outputs")
    parser.add_argument("--grid-cell-size-m", type=float, default=400.0)
    parser.add_argument("--grid-min-sold", type=int, default=5)
    parser.add_argument("--grid-min-active", type=int, default=3)
    parser.add_argument("--grid-export-geojson", action="store_true")
    parser.add_argument("--max-missing-sqft-pct", type=float, default=35.0)
    parser.add_argument("--max-missing-year-built-pct", type=float, default=50.0)
    parser.add_argument("--max-missing-price-pct", type=float, default=35.0)
    parser.add_argument("--max-rental-contamination-count", type=int, default=0)
    args = parser.parse_args()

    snapshot = find_snapshot(args.snapshot)
    inputs = args.inputs or [snapshot / "raw" / "har"]

    print(f"Pipeline snapshot: {snapshot}")
    print("Step 1/5: extract-har")
    extract_result = extract_snapshot(
        inputs=inputs,
        snapshot_path=snapshot,
        replay_failures=args.replay_failures,
        replay_timeout=args.replay_timeout,
    )
    print(
        f"  extracted payloads={extract_result['extracted_payloads']} listings={extract_result['merged_listings']}"
    )
    parse_failures = extract_result.get("parse_failures", [])
    if parse_failures:
        print(f"  parse failures={len(parse_failures)}")
        if args.replay_failures:
            print(
                f"  replay attempts={extract_result.get('replay_attempted', 0)} "
                f"successful={extract_result.get('replay_succeeded', 0)}"
            )
            replay_counts = extract_result.get("replay_result_counts", {})
            if replay_counts:
                print(f"  replay outcomes={replay_counts}")

    print("Step 2/5: normalize")
    norm_result = normalize_snapshot(snapshot)
    print(f"  normalized rows={norm_result['normalized_rows']}")

    print("Step 3/5: qa")
    qa_result = run_qa(
        snapshot=snapshot,
        max_missing_sqft_pct=args.max_missing_sqft_pct,
        max_missing_year_built_pct=args.max_missing_year_built_pct,
        max_missing_price_pct=args.max_missing_price_pct,
        max_rental_contamination_count=args.max_rental_contamination_count,
    )
    print(f"  qa status={'PASS' if qa_result['passed'] else 'FAIL'}")
    for warning in qa_result.get("warnings", []):
        print(f"  warning: {warning}")
    if qa_result["failures"]:
        for failure in qa_result["failures"]:
            print(f"  - {failure}")

    if not qa_result["passed"] and not args.force:
        raise SystemExit("QA failed. Re-run with --force to continue to analysis.")

    print("Step 4/5: analyze")
    analysis_result = run_analysis(
        snapshot=snapshot,
        force=args.force,
        min_cohort=args.min_cohort,
        require_flip_box=not args.include_non_flip_box,
        max_list_price=args.max_list_price,
        min_upside_to_p70=args.min_upside_to_p70,
        zip_whitelist=parse_zip_whitelist(args.zip_whitelist),
    )
    print(
        "  outputs: "
        f"ranked={analysis_result['ranked_path']}, "
        f"scoreboard={analysis_result['scoreboard_path']}, "
        f"streets={analysis_result['streets_path']}"
    )

    if args.skip_grid_analysis:
        return

    print("Step 5/5: grid-analysis")
    grid_result = run_grid_analysis(
        snapshot=snapshot,
        force=args.force,
        cell_size_m=args.grid_cell_size_m,
        min_sold=args.grid_min_sold,
        min_active=args.grid_min_active,
        export_geojson=args.grid_export_geojson,
        zip_whitelist=parse_zip_whitelist(args.zip_whitelist),
    )
    print(
        "  outputs: "
        f"grid_scoreboard={grid_result['scoreboard_path']}, "
        f"grid_candidates={grid_result['candidates_path']}, "
        f"grid_streets={grid_result['streets_path']}"
    )
    if "geojson_path" in grid_result:
        print(f"  grid_geojson={grid_result['geojson_path']}")


if __name__ == "__main__":
    main()
