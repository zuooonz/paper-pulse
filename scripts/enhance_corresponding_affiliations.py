"""Backfill corresponding-author geography, institution type and department."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys

from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from paper_library.corresponding_affiliation import (  # noqa: E402
    CorrespondingAffiliation,
    choose_primary_affiliation,
    extract_department,
    extract_institution_type,
    normalize_author_geography,
)
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session  # noqa: E402
from core.entity_detector import get_target_label  # noqa: E402
from paper_library.affiliation_geography_data import CITY_COORDS, UNI_CITY  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    parser.add_argument("--entity-label", default=get_target_label(), help="Limit enrichment to an entity label")
    parser.add_argument("--paper-ids", help="Comma-separated papers.id values")
    parser.add_argument("--dry-run", action="store_true", help="Calculate changes without committing them")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing structured values")
    parser.add_argument(
        "--classification-only", action="store_true",
        help="Update institution type and department without changing geography",
    )
    return parser.parse_args()


def _paper_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(value.strip()) for value in raw.split(",") if value.strip().isdigit()]


def main() -> int:
    args = parse_args()
    prepare_database(args, workspace=str(ROOT))
    if args.entity_label is None:
        raise SystemExit("Target entity is not configured; pass --entity-label to select an entity.")
    requested_ids = _paper_ids(args.paper_ids)
    stats: Counter[str] = Counter()

    with get_session(str(ROOT), "config") as session:
        conditions = ["p.entity_label = :entity_label"]
        params: dict[str, object] = {"entity_label": args.entity_label}
        if requested_ids:
            placeholders = ",".join(str(value) for value in requested_ids)
            conditions.append(f"p.id IN ({placeholders})")

        rows = session.execute(text(f"""
            SELECT p.id AS paper_id, p.corresponding_country AS paper_country,
                   p.corresponding_province, p.corresponding_city,
                   p.corresponding_institution_type, p.corresponding_department,
                   a.id AS author_id, pa.author_order,
                   a.affiliation_zh, a.affiliation,
                   a.country AS author_country, a.province AS author_province,
                   a.city AS author_city,
                   al.country AS location_country, al.province AS location_province,
                   al.city AS location_city,
                   a.institution_type, a.department
            FROM papers p
            JOIN paper_authors pa ON pa.paper_id = p.id AND pa.is_corresponding IS TRUE
            JOIN authors a ON a.id = pa.author_id
            LEFT JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE {' AND '.join(conditions)}
            ORDER BY p.id, pa.author_order, a.id
        """), params).mappings().all()

        grouped: dict[int, list] = defaultdict(list)
        for row in rows:
            grouped[row["paper_id"]].append(row)
            inst_type = extract_institution_type(row["affiliation_zh"], row["affiliation"])
            department = extract_department(row["affiliation_zh"], row["affiliation"])
            geography = normalize_author_geography(
                row["affiliation_zh"], row["affiliation"],
                row["location_country"], row["location_province"], row["location_city"],
                CITY_COORDS, UNI_CITY,
            )
            author_updates = {}
            if not args.classification_only:
                for key, value in {
                    "country": geography.country,
                    "province": geography.province,
                    "city": geography.city,
                }.items():
                    current = row[f"author_{key}"]
                    if value and value != current:
                        author_updates[key] = value
            if args.overwrite and inst_type != row["institution_type"]:
                author_updates["institution_type"] = inst_type
            elif inst_type and not row["institution_type"]:
                author_updates["institution_type"] = inst_type
            if args.overwrite and department != row["department"]:
                author_updates["department"] = department
            elif department and not row["department"]:
                author_updates["department"] = department
            if author_updates:
                if not args.dry_run:
                    assignments = ", ".join(f"{key} = :{key}" for key in author_updates)
                    session.execute(
                        text(f"UPDATE authors SET {assignments} WHERE id = :author_id"),
                        {**author_updates, "author_id": row["author_id"]},
                    )
                stats["authors_updated"] += 1

        for paper_id, paper_rows in grouped.items():
            normalized_geo = {
                row["author_id"]: normalize_author_geography(
                    row["affiliation_zh"], row["affiliation"],
                    row["location_country"], row["location_province"], row["location_city"],
                    CITY_COORDS, UNI_CITY,
                )
                for row in paper_rows
            }
            candidates = [
                CorrespondingAffiliation(
                    author_id=row["author_id"],
                    author_order=row["author_order"],
                    affiliation_zh=row["affiliation_zh"],
                    affiliation_en=row["affiliation"],
                    country=normalized_geo[row["author_id"]].country,
                    province=normalized_geo[row["author_id"]].province,
                    city=normalized_geo[row["author_id"]].city,
                )
                for row in paper_rows
            ]
            primary = choose_primary_affiliation(candidates)
            if not primary:
                continue
            original = paper_rows[0]
            values = {
                "corresponding_institution_type": extract_institution_type(
                    primary.affiliation_zh, primary.affiliation_en
                ),
                "corresponding_department": extract_department(
                    primary.affiliation_zh, primary.affiliation_en
                ),
            }
            if not args.classification_only:
                values.update({
                    "corresponding_country": primary.country,
                    "corresponding_province": primary.province,
                    "corresponding_city": primary.city,
                })
            updates = {}
            for key, value in values.items():
                original_key = "paper_country" if key == "corresponding_country" else key
                current = original[original_key]
                if key in {"corresponding_country", "corresponding_province", "corresponding_city"}:
                    if value and value != current:
                        updates[key] = value
                elif args.overwrite and value != current:
                    updates[key] = value
                elif value and not current:
                    updates[key] = value
            if updates:
                if not args.dry_run:
                    assignments = ", ".join(f"{key} = :{key}" for key in updates)
                    session.execute(
                        text(f"UPDATE papers SET {assignments} WHERE id = :paper_id"),
                        {**updates, "paper_id": paper_id},
                    )
                stats["papers_updated"] += 1
                for key in updates:
                    stats[key] += 1

        if args.dry_run:
            session.rollback()

    mode = "DRY-RUN" if args.dry_run else "UPDATED"
    print(f"[{mode}] entity_label={args.entity_label} papers_with_corresponding_authors={len(grouped)}")
    for key in sorted(stats):
        print(f"{key}={stats[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
