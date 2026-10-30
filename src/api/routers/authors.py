"""Authors API router — geo data + collaboration network.

Uses canonical_affiliation_id for clean joins — no more fuzzy matching.
Paper counting: only lead authors (corresponding + first author).
"""
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from paper_library.db import get_session
from paper_library.china_regions import CHINA_PROVINCES  # noqa: E402
from core.entity_detector import get_target_label  # noqa: E402
from ..dependencies import get_current_user  # noqa: E402

router = APIRouter(prefix="/api/authors", tags=["authors"])

TRUSTED_GEO_EXISTS_SQL = """
    EXISTS (
        SELECT 1 FROM affiliation_geo_evidence trusted_geo
        WHERE trusted_geo.affiliation_location_id = al.id
          AND trusted_geo.audit_status IN (
              'confirmed', 'corroborates',
              'accepted_correction', 'accepted_manual'
          )
    )
"""


def _is_lead_author(is_corresponding, is_first) -> bool:
    return bool(is_corresponding)  # 仅通讯作者


def _build_geo_filters(
    country: Optional[str], province: Optional[str], city: Optional[str],
) -> tuple[str, dict]:
    """Build SQL filter clauses for geographic dimensions."""
    clauses = []
    params = {}
    if country:
        country_en = CN_TO_EN_COUNTRY.get(country) if country else country
        if country_en:
            clauses.append("al.country = :country")
            params["country"] = country_en
    if province:
        clauses.append("al.province = :province")
        params["province"] = province
    if city:
        clauses.append("al.city = :city")
        params["city"] = city
    geo_filter = (" AND " + " AND ".join(clauses)) if clauses else ""
    return geo_filter, params


def _build_author_search_filter(search: Optional[str], dialect_name: str) -> tuple[str, dict]:
    """Build an escaped, case-insensitive author/affiliation search clause."""
    term = (search or "").strip()
    if not term:
        return "", {}
    escaped = term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    operator = "ILIKE" if dialect_name == "postgresql" else "LIKE"
    fields = (
        "a.name", "a.name_zh", "a.affiliation", "a.affiliation_zh",
        "al.affiliation_zh", "al.canonical_name_en",
    )
    clauses = [f"{field} {operator} :author_search ESCAPE '!'" for field in fields]
    return " AND (" + " OR ".join(clauses) + ")", {"author_search": f"%{escaped}%"}


# ── Country filter ──────────────────────────────────────────────

# Chinese→English mappings for country filter (frontend sends Chinese names)
CN_TO_EN_COUNTRY: dict[str, str] = {
    "中国": "China", "美国": "United States", "日本": "Japan",
    "韩国": "South Korea", "德国": "Germany", "英国": "United Kingdom",
    "加拿大": "Canada", "澳大利亚": "Australia", "法国": "France",
    "意大利": "Italy", "西班牙": "Spain", "荷兰": "Netherlands",
    "瑞士": "Switzerland", "瑞典": "Sweden", "新加坡": "Singapore",
    "印度": "India", "巴西": "Brazil", "土耳其": "Turkey",
    "伊朗": "Iran", "以色列": "Israel", "比利时": "Belgium",
    "丹麦": "Denmark", "芬兰": "Finland", "挪威": "Norway",
    "波兰": "Poland", "俄罗斯": "Russia", "奥地利": "Austria",
    "葡萄牙": "Portugal", "墨西哥": "Mexico",
}

COUNTRY_LIST = sorted(CN_TO_EN_COUNTRY.keys())


def _scope_filter(scope: str) -> tuple[str, dict]:
    """Build a whitelisted scope clause and bind configured target values."""
    target_label = get_target_label()
    if scope == "none":
        return " AND 1 = 0", {}
    if scope == "target":
        if not target_label:
            return " AND 1 = 0", {}
        return " AND p.entity_label = :scope_target_label", {"scope_target_label": target_label}
    if scope == "non_target" and target_label:
        return " AND (p.entity_label IS NULL OR p.entity_label != :scope_target_label)", {"scope_target_label": target_label}
    return "", {}


def _paper_visibility_filter(user: dict, alias: str = "p") -> str:
    """Keep author aggregates aligned with paper visibility permissions."""
    role = user.get("role")
    if role == "limited":
        return f" AND {alias}.review_outcome = 'approved'"
    if role not in {"admin", "reviewer"}:
        return f" AND {alias}.review_outcome != 'excluded'"
    return ""


# ══════════════════════════════════════════════════════════════════════
#  GET /api/authors/geo  —  map pin data
# ══════════════════════════════════════════════════════════════════════

@router.get("/geo")
def get_author_geo_data(
    country: Optional[str] = Query(default=None, description="Filter by country (Chinese name), e.g. 中国"),
    province: Optional[str] = Query(default=None, description="Filter by province, e.g. 广东"),
    city: Optional[str] = Query(default=None, description="Filter by city, e.g. 广州"),
    search: Optional[str] = None,
    scope: str = Query(default="all", description="Paper scope: target | non_target | all"),
    current_user: dict = Depends(get_current_user),
):
    """Map pins via canonical_affiliation_id — clean, fast, no fuzzy matching."""
    scope_filter, scope_params = _scope_filter(scope)
    visibility_filter = _paper_visibility_filter(current_user)
    geo_filter, geo_params = _build_geo_filters(country, province, city)
    query_params = {**geo_params, **scope_params}

    with get_session(str(Path.cwd()), "config") as session:
        search_filter, search_params = _build_author_search_filter(
            search, session.bind.dialect.name,
        )
        query_params.update(search_params)
        rows = session.execute(text(f"""
            SELECT a.id, a.name, a.name_zh, a.canonical_affiliation_id,
                   pa.paper_id, pa.is_corresponding, pa.is_first,
                   al.affiliation_zh,
                   al.country, al.province, al.city,
                   al.lat, al.lng
            FROM authors a
            JOIN paper_authors pa ON pa.author_id = a.id
            JOIN papers p ON p.id = pa.paper_id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE a.canonical_affiliation_id IS NOT NULL
              AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
            {scope_filter}{visibility_filter}{geo_filter}{search_filter}
        """), query_params).fetchall()

    # Institution label, geography and coordinates come from one trusted row.
    inst: dict[tuple, dict] = {}
    for row in rows:
        aid, name, name_zh, cid, pid, is_corr, is_first = (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6])
        if not _is_lead_author(is_corr, is_first):
            continue
        affiliation, effective_country, effective_province, effective_city, lat, lng = row[7:13]
        key = (cid, effective_country or "", effective_province or "", effective_city or "")
        entry = inst.setdefault(key, {
            "affiliation": affiliation or "", "country": effective_country or "",
            "city": effective_city or "", "province": effective_province or "",
            "lat": lat, "lng": lng, "authors": {}, "paper_ids": set(),
        })
        if aid not in entry["authors"]:
            entry["authors"][aid] = {
                "author_id": aid, "name": name or "", "name_zh": name_zh or "",
                "is_first": False, "is_corresponding": False,
            }
        if is_first:
            entry["authors"][aid]["is_first"] = True
        if is_corr:
            entry["authors"][aid]["is_corresponding"] = True
        entry["paper_ids"].add(pid)

    result = []
    for data in inst.values():
        if not data["authors"]:
            continue
        alist = []
        for a in data["authors"].values():
            role = "first_author" if a["is_first"] else (
                "corresponding" if a["is_corresponding"] else "co_author")
            alist.append({
                "author_id": a["author_id"], "name": a["name"],
                "name_zh": a["name_zh"], "role": role,
            })
        result.append({
            "affiliation": data["affiliation"], "city": data["city"],
            "province": data["province"], "country": data["country"],
            "lat": data["lat"], "lng": data["lng"],
            "author_count": len(data["authors"]),
            "paper_count": len(data["paper_ids"]),
            "authors": alist,
        })

    result.sort(key=lambda x: x["paper_count"], reverse=True)
    return {"locations": result, "total_locations": len(result)}


# ══════════════════════════════════════════════════════════════════════
#  GET /api/authors/network  —  collaboration graph
# ══════════════════════════════════════════════════════════════════════

@router.get("/network")
def get_collaboration_network(
    min_papers: int = Query(2, ge=1),
    top_n: int = Query(120, ge=10, le=500),
    country: Optional[str] = Query(default=None, description="Filter by country (Chinese name), e.g. 中国"),
    province: Optional[str] = Query(default=None, description="Filter by province, e.g. 广东"),
    city: Optional[str] = Query(default=None, description="Filter by city, e.g. 广州"),
    search: Optional[str] = None,
    scope: str = Query(default="all", description="Paper scope: target | non_target | all"),
    current_user: dict = Depends(get_current_user),
):
    """Collaboration among canonical institutions (lead authors only)."""
    scope_filter, scope_params = _scope_filter(scope)
    visibility_filter = _paper_visibility_filter(current_user)
    # Build country/city filter for rows query (not locs, to avoid 500)
    geo_where = ""
    geo_params = {}
    if country:
        c = CN_TO_EN_COUNTRY.get(country)
        if c:
            geo_where += " AND al.country = :country"
            geo_params["country"] = c
    if province:
        geo_where += " AND al.province = :province"
        geo_params["province"] = province
    if city:
        geo_where += " AND al.city = :city"
        geo_params["city"] = city

    query_params = {**geo_params, **scope_params}
    with get_session(str(Path.cwd()), "config") as session:
        search_filter, search_params = _build_author_search_filter(
            search, session.bind.dialect.name,
        )
        query_params.update(search_params)
        locs = session.execute(text(f"""
            SELECT id, affiliation_zh, city, lat, lng
            FROM affiliation_locations al
            WHERE lat IS NOT NULL AND lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
        """)).fetchall()

        rows = session.execute(text(
            "SELECT a.canonical_affiliation_id, pa.paper_id, pa.is_corresponding, pa.is_first "
            "FROM authors a JOIN paper_authors pa ON pa.author_id = a.id "
            "JOIN papers p ON p.id = pa.paper_id "
            "JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id "
            "WHERE a.canonical_affiliation_id IS NOT NULL AND al.lat IS NOT NULL "
            f"AND {TRUSTED_GEO_EXISTS_SQL} "
            + scope_filter + visibility_filter + geo_where + search_filter
        ), query_params if query_params else None).fetchall()

    # Build index
    loc_by_id: dict[int, dict] = {}
    for r in locs:
        loc_by_id[r[0]] = {"name": r[1], "city": r[2] or "", "lat": r[3], "lng": r[4]}

    # Paper → canonical_institution_ids
    paper_insts: dict[int, set[int]] = defaultdict(set)
    inst_papers: dict[int, set[int]] = defaultdict(set)
    inst_partners: dict[int, set[int]] = defaultdict(set)

    for cid, pid, is_corr, is_first in rows:
        if not _is_lead_author(is_corr, is_first):
            continue
        if cid is None:
            continue
        paper_insts[pid].add(cid)
        inst_papers[cid].add(pid)

    # Build edges
    edge_weights: dict[tuple, int] = defaultdict(int)
    for pid, insts in paper_insts.items():
        ilist = sorted(insts)
        for i in range(len(ilist)):
            for j in range(i + 1, len(ilist)):
                a, b = ilist[i], ilist[j]
                key = (a, b) if a < b else (b, a)
                edge_weights[key] += 1
                inst_partners[a].add(b)
                inst_partners[b].add(a)

    # Filter & rank — prioritize institutions with many collaborations
    filtered = [(a, b, w) for (a, b), w in edge_weights.items() if w >= min_papers]
    # Score = paper_count * 1 + sum of all edge weights (collaboration bonus)
    inst_score: dict[int, float] = {}
    for cid in inst_papers:
        edge_bonus = sum(w for (a, b), w in edge_weights.items() if (a == cid or b == cid) and w >= min_papers)
        inst_score[cid] = len(inst_papers[cid]) + edge_bonus * 2
    ranked = sorted(inst_papers.keys(), key=lambda x: inst_score.get(x, 0), reverse=True)
    top_set = set(ranked[:top_n])

    edges_out, node_ids_in_edges = [], set()
    for a, b, w in filtered:
        if a in top_set and b in top_set:
            ia, ib = loc_by_id.get(a), loc_by_id.get(b)
            if ia and ib and ia.get("lat") and ib.get("lat"):
                edges_out.append({
                    "source": ia["name"], "target": ib["name"],
                    "weight": w,
                    "source_lat": ia["lat"], "source_lng": ia["lng"],
                    "target_lat": ib["lat"], "target_lng": ib["lng"],
                    "cross_city": ia.get("city") != ib.get("city"),
                })
                node_ids_in_edges.add(a)
                node_ids_in_edges.add(b)

    nodes_out = []
    for cid in top_set:
        if cid not in node_ids_in_edges:
            continue
        info = loc_by_id.get(cid, {})
        nodes_out.append({
            "id": info["name"], "city": info["city"],
            "lat": info["lat"], "lng": info["lng"],
            "paper_count": len(inst_papers.get(cid, set())),
            "partner_count": len(inst_partners.get(cid, set())),
        })

    all_w = [e["weight"] for e in edges_out]
    cross = sum(1 for e in edges_out if e["cross_city"])
    single = sum(1 for insts in paper_insts.values() if len(insts) == 1)
    multi = len(paper_insts) - single

    return {
        "nodes": nodes_out, "edges": edges_out,
        "total_nodes": len(nodes_out), "total_edges": len(edges_out),
        "cross_city_edges": cross, "same_city_edges": len(edges_out) - cross,
        "papers_with_collab": multi, "papers_solo": single,
        "max_weight": max(all_w) if all_w else 0,
    }


# ══════════════════════════════════════════════════════════════════════
#  GET /api/authors/stats
# ══════════════════════════════════════════════════════════════════════

@router.get("/stats")
def get_author_stats(
    country: Optional[str] = Query(default=None, description="Filter by country (Chinese name), e.g. 中国"),
    province: Optional[str] = Query(default=None, description="Filter by province, e.g. 广东"),
    city: Optional[str] = Query(default=None, description="Filter by city, e.g. 广州"),
    search: Optional[str] = None,
    scope: str = Query(default="all", description="Paper scope: target | non_target | all"),
    current_user: dict = Depends(get_current_user),
):
    scope_filter, scope_params = _scope_filter(scope)
    visibility_filter = _paper_visibility_filter(current_user)
    geo_filter, geo_params = _build_geo_filters(country, province, city)
    query_params = {**geo_params, **scope_params}

    with get_session(str(Path.cwd()), "config") as session:
        search_filter, search_params = _build_author_search_filter(
            search, session.bind.dialect.name,
        )
        query_params.update(search_params)
        # Keep stats aligned with the map: corresponding authors and trusted
        # canonical geography only.
        total_authors = session.execute(text(f"""
            SELECT COUNT(DISTINCT a.id) FROM authors a
            JOIN paper_authors pa ON a.id = pa.author_id
            JOIN papers p ON pa.paper_id = p.id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE pa.is_corresponding IS TRUE AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
            {geo_filter}{search_filter} {scope_filter}{visibility_filter}
        """), query_params).scalar()

        total_cities = session.execute(text(f"""
            SELECT COUNT(DISTINCT al.city)
            FROM authors a
            JOIN paper_authors pa ON a.id = pa.author_id
            JOIN papers p ON pa.paper_id = p.id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE pa.is_corresponding IS TRUE AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
            {geo_filter}{search_filter} {scope_filter}{visibility_filter}
        """), query_params).scalar()

        total_institutions = session.execute(text(f"""
            SELECT COUNT(DISTINCT al.id) FROM authors a
            JOIN paper_authors pa ON a.id = pa.author_id
            JOIN papers p ON pa.paper_id = p.id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE pa.is_corresponding IS TRUE AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
            {geo_filter}{search_filter} {scope_filter}{visibility_filter}
        """), query_params).scalar()

        total_papers = session.execute(text(f"""
            SELECT COUNT(DISTINCT pa.paper_id) FROM paper_authors pa
            JOIN authors a ON a.id = pa.author_id
            JOIN papers p ON p.id = pa.paper_id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE pa.is_corresponding IS TRUE AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
            {geo_filter}{search_filter} {scope_filter}{visibility_filter}
        """), query_params).scalar()

        geo_review_pending = session.execute(text("""
            SELECT COUNT(DISTINCT affiliation_location_id)
            FROM affiliation_geo_evidence
            WHERE audit_status IN ('needs_review', 'provider_conflict', 'no_result')
        """)).scalar()

    return {
        "total_authors": total_authors,
        "total_cities": total_cities,
        "total_institutions": total_institutions,
        "total_papers_mapped": total_papers,
        "geo_review_pending": geo_review_pending,
    }


@router.get("/profile/{author_id}")
def get_author_profile(
    author_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return paper roles for one author."""
    visibility_filter = _paper_visibility_filter(current_user)
    with get_session(str(Path.cwd()), "config") as session:
        author = session.execute(text("""
            SELECT a.id, a.name, a.name_zh, a.affiliation, a.affiliation_zh,
                   al.country, al.province, al.city,
                   al.affiliation_zh AS canonical_affiliation,
                   a.email, a.orcid
            FROM authors a
            LEFT JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE a.id = :author_id
        """), {"author_id": author_id}).mappings().first()
        if author is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Author not found")
        rows = session.execute(text(f"""
            SELECT p.id AS paper_id, p.internal_id, p.title, p.title_zh, p.doi,
                   p.year, pa.is_first, pa.is_corresponding
            FROM paper_authors pa
            JOIN papers p ON p.id = pa.paper_id
            WHERE pa.author_id = :author_id{visibility_filter}
            ORDER BY COALESCE(p.year, 0) DESC, p.internal_id DESC
        """), {"author_id": author_id}).mappings().all()
    papers = {}
    for row in rows:
        papers.setdefault(row["paper_id"], {
            "paper_id": row["paper_id"], "internal_id": row["internal_id"],
            "title": row["title"], "title_zh": row["title_zh"], "doi": row["doi"],
            "year": row["year"], "is_first": bool(row["is_first"]),
            "is_corresponding": bool(row["is_corresponding"]),
        })
    return {"author": dict(author), "papers": list(papers.values())}


@router.get("/review/geography")
def get_geography_review_queue(
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """Expose unresolved map-provider conflicts without silently applying them."""
    del current_user
    with get_session(str(Path.cwd()), "config") as session:
        total = session.execute(text("""
            SELECT COUNT(DISTINCT affiliation_location_id)
            FROM affiliation_geo_evidence
            WHERE audit_status IN ('needs_review', 'provider_conflict', 'no_result')
        """)).scalar()
        rows = session.execute(text("""
            SELECT e.affiliation_location_id, al.affiliation_zh,
                   al.province AS current_province, al.city AS current_city,
                   e.provider, e.result_name, e.province, e.city, e.district,
                   e.provider_confidence, e.conflict_reason, e.audit_status
            FROM affiliation_geo_evidence e
            JOIN affiliation_locations al ON al.id = e.affiliation_location_id
            WHERE e.audit_status IN ('needs_review', 'provider_conflict', 'no_result')
              AND e.affiliation_location_id IN (
                  SELECT affiliation_location_id FROM affiliation_geo_evidence
                  WHERE audit_status IN ('needs_review', 'provider_conflict', 'no_result')
                  GROUP BY affiliation_location_id ORDER BY affiliation_location_id
                  LIMIT :limit
              )
            ORDER BY e.affiliation_location_id, e.provider
        """), {"limit": limit}).mappings().all()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row["affiliation_location_id"], {
            "affiliation_location_id": row["affiliation_location_id"],
            "affiliation": row["affiliation_zh"],
            "current_province": row["current_province"],
            "current_city": row["current_city"], "candidates": [],
        })
        item["candidates"].append({key: row[key] for key in (
            "provider", "result_name", "province", "city", "district",
            "provider_confidence", "conflict_reason", "audit_status",
        )})
    return {"items": list(grouped.values()), "total": total}


class GeographyReviewDecision(BaseModel):
    provider: str


@router.post("/review/geography/{location_id}")
def accept_geography_candidate(
    location_id: int,
    decision: GeographyReviewDecision,
    current_user: dict = Depends(get_current_user),
):
    """Apply one reviewed provider candidate and keep an auditable decision."""
    if current_user.get("role") not in {"admin", "reviewer", "viewer"}:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Geography reviewer role required")
    with get_session(str(Path.cwd()), "config") as session:
        candidate = session.execute(text("""
            SELECT id, province, city, lat, lng, coordinate_system
            FROM affiliation_geo_evidence
            WHERE affiliation_location_id=:location_id AND provider=:provider
              AND audit_status IN ('needs_review', 'provider_conflict')
            ORDER BY checked_at DESC LIMIT 1
        """), {"location_id": location_id, "provider": decision.provider}).mappings().first()
        if candidate is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Geography candidate not found")
        values = {"id": location_id, "province": candidate["province"],
                  "city": candidate["city"]}
        coordinate_sql = ""
        if candidate["coordinate_system"] == "GCJ-02" and candidate["lat"] is not None:
            coordinate_sql = ", lat=:lat, lng=:lng, geocoded_at=CURRENT_TIMESTAMP"
            values.update(lat=candidate["lat"], lng=candidate["lng"])
        session.execute(text(f"""
            UPDATE affiliation_locations
            SET province=:province, city=:city, country='China' {coordinate_sql}
            WHERE id=:id
        """), values)
        session.execute(text("""
            UPDATE authors SET province=:province, city=:city, country='China'
            WHERE canonical_affiliation_id=:id
        """), values)
        session.execute(text("""
            UPDATE affiliation_geo_evidence
            SET audit_status=CASE WHEN id=:evidence_id THEN 'accepted_manual' ELSE 'rejected_manual' END,
                conflict_reason=COALESCE(conflict_reason, '') || :actor
            WHERE affiliation_location_id=:location_id
              AND audit_status IN ('needs_review', 'provider_conflict')
        """), {"evidence_id": candidate["id"], "location_id": location_id,
                "actor": f"; reviewed_by:{current_user.get('username', 'authenticated_user')}"})
    return {"status": "accepted", "location_id": location_id,
            "provider": decision.provider}


# ══════════════════════════════════════════════════════════════════════
#  GET /api/authors/provinces  —  province list for dropdown
# ══════════════════════════════════════════════════════════════════════

@router.get("/provinces")
def get_provinces(
    country: Optional[str] = Query(default=None, description="Filter by country"),
    current_user: dict = Depends(get_current_user),
):
    """Return distinct provinces for the given country."""
    geo_filter, geo_params = _build_geo_filters(country, None, None)
    visibility_filter = _paper_visibility_filter(current_user)
    with get_session(str(Path.cwd()), "config") as session:
        rows = session.execute(text(f"""
            SELECT DISTINCT al.province AS province
            FROM authors a
            JOIN paper_authors pa ON pa.author_id = a.id
            JOIN papers p ON p.id = pa.paper_id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE pa.is_corresponding IS TRUE AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
              AND al.province IS NOT NULL AND al.province != '' {visibility_filter}{geo_filter}
            ORDER BY province
        """), geo_params).fetchall()
    provinces = [r[0] for r in rows]
    if country in {"中国", "China"}:
        # Keep the picker complete even when no stored affiliation has yet
        # contributed a province; Taiwan is one of the 34 divisions.
        merged = list(CHINA_PROVINCES)
        merged.extend(value for value in provinces if value not in merged)
        provinces = merged
    return {"provinces": provinces}


# ══════════════════════════════════════════════════════════════════════
#  GET /api/authors/cities  —  city list for dropdown
# ══════════════════════════════════════════════════════════════════════

@router.get("/cities")
def get_cities(
    country: Optional[str] = Query(default=None),
    province: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Return distinct cities for the given country/province."""
    geo_filter, geo_params = _build_geo_filters(country, province, None)
    visibility_filter = _paper_visibility_filter(current_user)
    with get_session(str(Path.cwd()), "config") as session:
        rows = session.execute(text(f"""
            SELECT DISTINCT al.city AS city
            FROM authors a
            JOIN paper_authors pa ON pa.author_id = a.id
            JOIN papers p ON p.id = pa.paper_id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE pa.is_corresponding IS TRUE AND al.lat IS NOT NULL AND al.lng IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}
              AND al.city IS NOT NULL AND al.city != '' {visibility_filter}{geo_filter}
            ORDER BY city
        """), geo_params).fetchall()
    return {"cities": [r[0] for r in rows]}


# ══════════════════════════════════════════════════════════════════════
#  GET /api/authors/export  —  CSV export
# ══════════════════════════════════════════════════════════════════════

@router.get("/export")
def export_authors(
    country: Optional[str] = Query(default=None),
    province: Optional[str] = Query(default=None),
    city: Optional[str] = Query(default=None),
    search: Optional[str] = None,
    scope: str = Query(default="all", description="Paper scope: target | non_target | all"),
    current_user: dict = Depends(get_current_user),
):
    """Export filtered author data as CSV."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    scope_filter, scope_params = _scope_filter(scope)
    visibility_filter = _paper_visibility_filter(current_user)
    geo_filter, geo_params = _build_geo_filters(country, province, city)
    query_params = {**geo_params, **scope_params, "target_label": get_target_label() or ""}

    with get_session(str(Path.cwd()), "config") as session:
        search_filter, search_params = _build_author_search_filter(
            search, session.bind.dialect.name,
        )
        query_params.update(search_params)
        from sqlalchemy import literal_column
        from paper_library.sql_dialect import bool_any, group_concat_distinct

        sql_dialect = session.bind.dialect
        dois_aggregate = str(group_concat_distinct(
            literal_column("p.doi"), ","
        ).compile(dialect=sql_dialect, compile_kwargs={"literal_binds": True}))
        pmids_aggregate = str(group_concat_distinct(
            literal_column("p.pmid"), ","
        ).compile(dialect=sql_dialect, compile_kwargs={"literal_binds": True}))
        corresponding_aggregate = str(bool_any(
            literal_column("pa.is_corresponding")
        ).compile(dialect=sql_dialect, compile_kwargs={"literal_binds": True}))
        first_aggregate = str(bool_any(
            literal_column("pa.is_first")
        ).compile(dialect=sql_dialect, compile_kwargs={"literal_binds": True}))
        rows = session.execute(text(f"""
            SELECT a.name, a.name_zh, al.affiliation_zh,
                   al.city, al.province, al.country,
                   {corresponding_aggregate} as is_corresponding,
                   {first_aggregate} as is_first,
                   COUNT(DISTINCT pa.paper_id) as paper_count,
                   SUM(CASE WHEN p.entity_label = :target_label THEN 1 ELSE 0 END) as target_paper_count,
                   {dois_aggregate} as dois,
                   {pmids_aggregate} as pmids
            FROM authors a
            JOIN paper_authors pa ON pa.author_id = a.id
            JOIN papers p ON p.id = pa.paper_id
            JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
            WHERE a.canonical_affiliation_id IS NOT NULL
              AND {TRUSTED_GEO_EXISTS_SQL}{geo_filter}{search_filter} {scope_filter}{visibility_filter}
            GROUP BY a.id, a.name, a.name_zh,
                     al.id, al.affiliation_zh, al.city, al.province, al.country
            ORDER BY al.country, al.province, al.city, paper_count DESC
        """), query_params).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "姓名", "中文名", "单位", "城市", "省份", "国家",
            "角色", "论文数", "目标课题论文数", "DOI", "PMID",
        ])
        for r in rows:
            role = "通讯作者" if r[6] else ("第一作者" if r[7] else "合作作者")
            writer.writerow([
                r[0] or "", r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5] or "",
                role, r[8], r[9], r[10] or "", r[11] or "",
            ])

        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8-sig")),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=authors.csv"},
        )
