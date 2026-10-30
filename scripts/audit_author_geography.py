#!/usr/bin/env python3
"""Audit author-library affiliation locations against independent map evidence."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from paper_library.affiliation_geography import (  # noqa: E402
    GeoCandidate,
    baidu_api_key,
    haversine_km,
    normalize_place,
    providers_agree,
    query_amap,
    query_baidu,
    query_nominatim,
    split_institution_department,
)
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session  # noqa: E402
from paper_library.models import AffiliationGeoEvidence  # noqa: E402
from core.service_config import configured_value  # noqa: E402
from core.entity_detector import get_target_label  # noqa: E402

AMAP_KEY = configured_value("AMAP_API_KEY", "maps", "amap_api_key")


def _save_evidence(session, location_id: int, candidate, status: str, reason: str):
    row = session.scalar(select(AffiliationGeoEvidence).where(
        AffiliationGeoEvidence.affiliation_location_id == location_id,
        AffiliationGeoEvidence.provider == candidate.provider,
        AffiliationGeoEvidence.query_text == candidate.query_text,
    ))
    if row is None:
        row = AffiliationGeoEvidence(
            affiliation_location_id=location_id, provider=candidate.provider,
            query_text=candidate.query_text,
        )
        session.add(row)
    for field in (
        "institution_query", "department_query", "result_name", "formatted_address",
        "country", "province", "city", "district", "lat", "lng",
        "coordinate_system", "precision", "raw_response",
    ):
        setattr(row, field, getattr(candidate, field))
    row.provider_confidence = candidate.confidence
    row.audit_status = status
    row.conflict_reason = reason or None
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    add_db_arguments(parser)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--internal-id", type=int)
    parser.add_argument("--second-provider", choices=("auto", "baidu", "nominatim"), default="auto")
    parser.add_argument("--only-missing-evidence", action="store_true")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))
    target_label = get_target_label()
    if not target_label:
        print("Target entity is not configured; nothing to audit.")
        return 0
    http = requests.Session()
    stats = Counter()
    corrections = []
    baidu_key = baidu_api_key()
    with get_session(str(ROOT), "config") as session:
        params = {}
        paper_filter = ""
        if args.internal_id is not None:
            paper_filter = " AND p.internal_id = :internal_id"
            params["internal_id"] = args.internal_id
        evidence_filter = ""
        if args.only_missing_evidence:
            evidence_filter = """
              AND NOT EXISTS (
                  SELECT 1 FROM affiliation_geo_evidence e
                  WHERE e.affiliation_location_id = al.id AND e.provider = 'amap'
              )
            """
        rows = session.execute(text(f"""
            SELECT DISTINCT al.id, al.affiliation_zh, al.city, al.province,
                   al.country, al.lat, al.lng
            FROM affiliation_locations al
            JOIN authors a ON a.canonical_affiliation_id = al.id
            JOIN paper_authors pa ON pa.author_id = a.id
            JOIN papers p ON p.id = pa.paper_id
            WHERE pa.is_corresponding IS TRUE AND p.entity_label = :target_label
              AND COALESCE(al.country, 'China') = 'China'
              AND TRIM(COALESCE(al.affiliation_zh, '')) != '' {paper_filter}
              {evidence_filter}
            ORDER BY al.id
        """), {**params, "target_label": target_label}).all()
        if args.limit:
            rows = rows[:args.limit]
        for index, row in enumerate(rows, 1):
            location_id, affiliation, old_city, old_province, _, old_lat, old_lng = row
            institution, department = split_institution_department(affiliation)
            if not institution:
                stats["invalid_affiliation"] += 1
                continue
            first = query_amap(affiliation, old_city or "", AMAP_KEY, http)
            if first is None:
                empty = GeoCandidate(
                    provider="amap", query_text=affiliation,
                    institution_query=institution, department_query=department,
                )
                _save_evidence(
                    session, location_id, empty, "no_result",
                    "Amap returned no reliable institution POI",
                )
                stats["amap_no_result"] += 1
                continue
            city_changed = bool(old_city and normalize_place(first.city) != normalize_place(old_city))
            province_changed = bool(
                old_province and first.province
                and normalize_place(first.province) != normalize_place(old_province)
            )
            distance = None
            if None not in (old_lat, old_lng, first.lat, first.lng):
                distance = haversine_km(old_lat, old_lng, first.lat, first.lng)
            suspicious = city_changed or province_changed or first.confidence < 0.72 or (
                distance is not None and distance > 30
            )
            if not suspicious:
                _save_evidence(session, location_id, first, "confirmed", "")
                stats["confirmed"] += 1
                if args.apply and first.confidence >= 0.82 and first.lat is not None:
                    session.execute(text("""
                        UPDATE affiliation_locations
                        SET lat=:lat, lng=:lng, geocoded_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                    """), {"lat": first.lat, "lng": first.lng, "id": location_id})
                    stats["coordinates_refreshed"] += 1
                continue

            provider = args.second_provider
            if provider == "auto":
                provider = "baidu" if baidu_key else "nominatim"
            second = (
                query_baidu(affiliation, first.city or old_city or "", baidu_key, http)
                if provider == "baidu"
                else query_nominatim(affiliation, first.city or old_city or "", http)
            )
            if provider == "nominatim":
                time.sleep(1.05)
            reason = "; ".join(filter(None, (
                f"city:{old_city}->{first.city}" if city_changed else "",
                f"province:{old_province}->{first.province}" if province_changed else "",
                f"distance_km:{distance:.1f}" if distance is not None and distance > 30 else "",
                f"confidence:{first.confidence:.2f}" if first.confidence < 0.72 else "",
            )))
            accepted = bool(second and providers_agree(first, second))
            _save_evidence(session, location_id, first,
                           "accepted_correction" if accepted else "needs_review", reason)
            if second:
                _save_evidence(session, location_id, second,
                               "corroborates" if accepted else "provider_conflict", reason)
            if accepted:
                stats["accepted_corrections"] += 1
                corrections.append({
                    "location_id": location_id, "affiliation": affiliation,
                    "old_city": old_city, "new_city": first.city,
                    "old_province": old_province, "new_province": first.province,
                    "providers": [first.provider, second.provider],
                })
                if args.apply:
                    session.execute(text("""
                        UPDATE affiliation_locations
                        SET city=:city, province=:province, country='China',
                            lat=:lat, lng=:lng, geocoded_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                    """), {"city": first.city, "province": first.province,
                            "lat": first.lat, "lng": first.lng, "id": location_id})
                    session.execute(text("""
                        UPDATE authors SET city=:city, province=:province, country='China'
                        WHERE canonical_affiliation_id=:id
                    """), {"city": first.city, "province": first.province,
                            "id": location_id})
            else:
                stats["needs_review"] += 1
            if index % 25 == 0:
                print(json.dumps({"progress": index, "total": len(rows), **stats}, ensure_ascii=False))
                session.flush()
        if not args.apply:
            session.rollback()
    print(json.dumps({"apply": args.apply, "total": len(rows), **stats,
                      "corrections": corrections}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
