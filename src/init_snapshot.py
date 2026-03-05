"""Initialize a snapshot pack folder for manual HAR capture workflows."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

SNAPSHOTS_DIR = Path("snapshots")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "snapshot"


def create_intake_payload(args: argparse.Namespace, snapshot_id: str) -> dict:
    bbox = {
        "nwlat": args.nwlat,
        "nwlng": args.nwlng,
        "selat": args.selat,
        "selng": args.selng,
    }
    return {
        "snapshot_id": snapshot_id,
        "label": args.label,
        "bbox": bbox,
        "intent": args.intent,
        "notes": args.notes,
    }


def ensure_dirs(root: Path) -> None:
    dirs = [
        root / "raw",
        root / "raw" / "har",
        root / "meta",
        root / "out",
        root / "out" / "extracted",
        root / "out" / "normalized",
        root / "out" / "qa",
        root / "out" / "analysis",
    ]
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a snapshot pack skeleton")
    parser.add_argument("name", nargs="?", help="Optional explicit snapshot folder name")
    parser.add_argument("--label", default="", help="Human-readable label for this snapshot")
    parser.add_argument(
        "--intent",
        nargs="+",
        default=["for_sale_active", "for_sale_sold", "for_rent_active"],
        help="Intended datasets to capture",
    )
    parser.add_argument("--nwlat", type=float, default=None)
    parser.add_argument("--nwlng", type=float, default=None)
    parser.add_argument("--selat", type=float, default=None)
    parser.add_argument("--selng", type=float, default=None)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y-%m-%d")
    default_slug = slugify(args.label) if args.label else "snapshot"
    snapshot_id = args.name or f"{ts}_{default_slug}"

    root = SNAPSHOTS_DIR / snapshot_id
    ensure_dirs(root)

    intake = create_intake_payload(args, snapshot_id)
    intake_path = root / "meta" / "intake.json"
    if not intake_path.exists():
        intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")

    notes_path = root / "raw" / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(
            "# Snapshot Notes\n\n- Driving observations:\n- Street-level risks:\n- Other context:\n",
            encoding="utf-8",
        )

    print(f"Created snapshot pack: {root}")
    print(f"HAR drop folder: {root / 'raw' / 'har'}")
    print(f"Intake metadata: {intake_path}")


if __name__ == "__main__":
    main()
