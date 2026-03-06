"""Render snapshot artifacts as terminal tables."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from textwrap import shorten
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

ARTIFACT_PATHS = {
    "ranked": Path("out/analysis/ranked_candidates.csv"),
    "scoreboard": Path("out/analysis/scoreboard_segments.csv"),
    "streets": Path("out/analysis/streets_top.csv"),
    "grid_scoreboard": Path("out/analysis/grid_scoreboard.csv"),
    "grid_candidates": Path("out/analysis/grid_candidates.csv"),
    "grid_streets": Path("out/analysis/grid_streets.csv"),
    "active": Path("out/normalized/active.csv"),
    "sold": Path("out/normalized/sold.csv"),
    "rentals": Path("out/normalized/rentals.csv"),
    "requests": Path("out/extracted/requests_index.csv"),
    "qa": Path("out/qa/qa_report.json"),
    "normalize": Path("out/normalized/normalize_report.json"),
}

ARTIFACT_ORDER = [
    "ranked",
    "scoreboard",
    "streets",
    "grid_scoreboard",
    "grid_candidates",
    "grid_streets",
    "active",
    "sold",
    "rentals",
    "requests",
    "qa",
    "normalize",
]

DEFAULT_COLUMNS = {
    "ranked": [
        "address",
        "zip",
        "list_price",
        "calc_ppsf_list",
        "sold_ppsf_p30",
        "sold_ppsf_p70",
        "upside_to_p70",
        "rank_score",
        "confidence_grade",
        "url",
    ],
    "scoreboard": [
        "zip",
        "era_bucket",
        "size_bucket",
        "sold_ppsf_p30",
        "sold_median_ppsf",
        "sold_ppsf_p70",
        "active_median_ppsf",
        "active_minus_sold",
        "count_sold",
        "sold_cap_severity",
    ],
    "streets": [
        "street_name",
        "zip",
        "sold_count_legacy",
        "active_count_legacy",
        "sold_median_ppsf_legacy",
        "median_dom_sold_legacy",
        "count_sold_flips",
        "sold_cap_severity",
    ],
    "grid_scoreboard": [
        "grid_id",
        "sold_count",
        "active_count",
        "sold_ppsf_p30",
        "sold_ppsf_p70",
        "active_median_ppsf",
        "renovation_spread",
        "active_minus_sold",
        "hunt_score",
        "grid_confidence_grade",
        "cell_label",
    ],
    "grid_candidates": [
        "grid_id",
        "address",
        "list_price",
        "calc_ppsf_list",
        "sold_ppsf_p30",
        "sold_ppsf_p70",
        "upside_to_p70",
        "rank_score",
        "confidence_grade",
        "max_offer_p70",
        "url",
    ],
    "grid_streets": [
        "grid_id",
        "street_name",
        "sold_count_legacy",
        "active_count_legacy",
        "sold_median_ppsf_legacy",
        "median_dom_sold_legacy",
        "new_construction_count",
        "street_score",
    ],
    "active": ["address", "zip", "list_price_num", "calc_ppsf_list", "beds", "sqft", "year_built", "url"],
    "sold": ["address", "zip", "sold_price_num", "calc_ppsf_sold", "beds", "sqft", "year_built", "dom", "url"],
    "rentals": ["address", "zip", "list_price_num", "beds", "sqft", "year_built", "url"],
    "requests": ["timestamp", "status", "url", "content_type", "bytes"],
}


def find_snapshot(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
        if path.exists():
            return path
        raise FileNotFoundError(f"Snapshot not found: {path}")

    root = Path("snapshots")
    candidates = [item for item in root.iterdir() if item.is_dir()] if root.exists() else []
    if not candidates:
        raise FileNotFoundError("No snapshots found.")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def resolve_artifact(snapshot: Path, artifact: str | None, path_arg: str | None) -> tuple[Path, str]:
    if path_arg:
        path = Path(path_arg)
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {path}")
        label = artifact or path.stem
        return path, label

    if not artifact:
        artifact = "ranked"
    if artifact not in ARTIFACT_PATHS:
        raise ValueError(f"Unknown artifact: {artifact}")

    path = snapshot / ARTIFACT_PATHS[artifact]
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {path}")
    return path, artifact


def resolve_artifacts(
    snapshot: Path,
    artifacts: list[str] | None,
    path_arg: str | None,
    include_all: bool,
) -> list[tuple[Path, str]]:
    if path_arg:
        path, label = resolve_artifact(snapshot, None, path_arg)
        return [(path, label)]

    chosen = artifacts or []
    if include_all:
        chosen = list(ARTIFACT_ORDER)
    elif not chosen:
        chosen = ["ranked"]

    resolved: list[tuple[Path, str]] = []
    for artifact in chosen:
        try:
            resolved.append(resolve_artifact(snapshot, artifact, None))
        except FileNotFoundError:
            if include_all:
                continue
            raise
    if not resolved:
        raise FileNotFoundError(f"No artifacts found under snapshot: {snapshot}")
    return resolved


def format_value(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def pick_columns(df: pd.DataFrame, artifact: str, columns_arg: str | None, all_columns: bool) -> pd.DataFrame:
    if df.empty:
        return df
    if all_columns:
        return df
    if columns_arg:
        wanted = [item.strip() for item in columns_arg.split(",") if item.strip()]
    else:
        wanted = DEFAULT_COLUMNS.get(artifact, [])
    if not wanted:
        return df
    selected = [column for column in wanted if column in df.columns]
    return df[selected] if selected else df


def table_lines(df: pd.DataFrame, title: str, limit: int, width: int | None = None) -> list[str]:
    limit = max(limit, 1)
    term_width = width or shutil.get_terminal_size((140, 40)).columns
    work = df.head(limit).copy()
    if work.empty:
        return [title, "(no rows)"]

    rendered = {
        column: [format_value(value) for value in work[column]]
        for column in work.columns
    }
    headers = list(work.columns)
    widths = {
        column: max(len(column), max((len(cell) for cell in cells), default=0))
        for column, cells in rendered.items()
    }

    min_width = 8
    total_width = sum(widths.values()) + (3 * (len(headers) - 1))
    while total_width > term_width and any(value > min_width for value in widths.values()):
        widest = max(widths, key=widths.get)
        if widths[widest] <= min_width:
            break
        widths[widest] -= 1
        total_width = sum(widths.values()) + (3 * (len(headers) - 1))

    def trim(text: str, max_width: int) -> str:
        return shorten(text, width=max_width, placeholder="...") if len(text) > max_width else text

    header_line = " | ".join(trim(column, widths[column]).ljust(widths[column]) for column in headers)
    divider = "-+-".join("-" * widths[column] for column in headers)
    rows = [
        " | ".join(trim(rendered[column][index], widths[column]).ljust(widths[column]) for column in headers)
        for index in range(len(work))
    ]

    lines = [title, header_line, divider, *rows]
    if len(df) > len(work):
        lines.append(f"... showing {len(work)} of {len(df)} rows")
    return lines


def render_csv(path: Path, artifact: str, limit: int, columns_arg: str | None, all_columns: bool, width: int | None) -> str:
    try:
        df = pd.read_csv(path)
    except EmptyDataError:
        df = pd.DataFrame()
    df = pick_columns(df, artifact, columns_arg, all_columns)
    title = f"{artifact}: {path}"
    return "\n".join(table_lines(df, title, limit=limit, width=width))


def render_qa_report(path: Path, width: int | None) -> str:
    report = json.loads(path.read_text(encoding="utf-8"))

    summary_rows = pd.DataFrame(
        [
            {"field": "snapshot_id", "value": report.get("snapshot_id")},
            {"field": "passed", "value": report.get("passed")},
            {"field": "warnings", "value": len(report.get("warnings", []))},
            {"field": "failures", "value": len(report.get("failures", []))},
            {"field": "sold_data_capped", "value": report.get("completeness", {}).get("sold", {}).get("likely_capped")},
            {"field": "sold_cap_severity", "value": report.get("completeness", {}).get("sold", {}).get("cap_severity")},
        ]
    )
    dataset_rows = []
    for name, metrics in report.get("datasets", {}).items():
        missingness = metrics.get("missingness", {})
        dataset_rows.append(
            {
                "dataset": name,
                "rows": metrics.get("rows"),
                "unique": metrics.get("unique_listings"),
                "dupes": metrics.get("duplicate_rows"),
                "sqft_missing_pct": missingness.get("sqft_pct"),
                "year_missing_pct": missingness.get("year_built_pct"),
                "price_missing_pct": missingness.get("price_pct"),
                "ppsf_mismatch_pct": metrics.get("ppsf_mismatch_pct"),
                "zip_distribution": metrics.get("zip_distribution"),
            }
        )

    sections = [
        "\n".join(table_lines(summary_rows, f"qa summary: {path}", limit=len(summary_rows), width=width)),
        "\n".join(table_lines(pd.DataFrame(dataset_rows), "qa datasets", limit=max(len(dataset_rows), 1), width=width)),
    ]
    warnings = report.get("warnings", [])
    if warnings:
        sections.append("warnings:\n" + "\n".join(f"- {item}" for item in warnings))
    failures = report.get("failures", [])
    if failures:
        sections.append("failures:\n" + "\n".join(f"- {item}" for item in failures))
    return "\n\n".join(sections)


def render_normalize_report(path: Path, width: int | None) -> str:
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for name, metrics in report.get("datasets", {}).items():
        rows.append(
            {
                "dataset": name,
                "raw_rows": metrics.get("raw_rows"),
                "deduped_rows": metrics.get("deduped_rows"),
                "duplicate_rows_removed": metrics.get("duplicate_rows_removed"),
                "raw_unique": metrics.get("raw", {}).get("unique_listing_ids"),
                "deduped_unique": metrics.get("deduped", {}).get("unique_listing_ids"),
            }
        )
    df = pd.DataFrame(rows)
    return "\n".join(table_lines(df, f"normalize report: {path}", limit=max(len(df), 1), width=width))


def render_json(path: Path, artifact: str, width: int | None) -> str:
    if artifact == "qa":
        return render_qa_report(path, width=width)
    if artifact == "normalize":
        return render_normalize_report(path, width=width)
    payload = json.loads(path.read_text(encoding="utf-8"))
    pretty = json.dumps(payload, indent=2)
    if width:
        return f"{artifact}: {path}\n{pretty}"
    return f"{artifact}: {path}\n{pretty}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize snapshot artifacts in the terminal")
    parser.add_argument("--snapshot", help="Snapshot pack path (defaults to latest)")
    parser.add_argument(
        "--artifact",
        choices=sorted(ARTIFACT_PATHS.keys()),
        action="append",
        help="Named artifact to render; repeat flag to render multiple artifacts",
    )
    parser.add_argument("--path", help="Direct path to a CSV or JSON artifact")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Render each available named artifact for the snapshot in sequence",
    )
    parser.add_argument("--limit", type=int, default=20, help="Maximum rows to show for table artifacts")
    parser.add_argument("--columns", help="Comma-separated columns to display for CSV artifacts")
    parser.add_argument("--all-columns", action="store_true", help="Show all columns for CSV artifacts")
    parser.add_argument("--width", type=int, help="Override detected terminal width")
    args = parser.parse_args()

    if args.path and (args.artifact or args.all):
        raise SystemExit("Use either --path or named artifacts (--artifact / --all), not both.")

    snapshot = find_snapshot(args.snapshot) if not args.path else Path(".")
    targets = resolve_artifacts(snapshot, args.artifact, args.path, args.all)

    outputs: list[str] = []
    for path, artifact in targets:
        if path.suffix.lower() == ".csv":
            output = render_csv(
                path=path,
                artifact=artifact,
                limit=args.limit,
                columns_arg=args.columns,
                all_columns=args.all_columns,
                width=args.width,
            )
        elif path.suffix.lower() == ".json":
            output = render_json(path=path, artifact=artifact, width=args.width)
        else:
            raise SystemExit(f"Unsupported artifact type: {path.suffix}")
        outputs.append(output)

    sys.stdout.write("\n\n".join(outputs))
    if outputs:
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
