"""Paper listing API routes."""

from fastapi import APIRouter

from paper_library.models import Author
from fastapi import Depends
from typing import Optional
from paper_library.models import Paper
from fastapi import Query
from sqlalchemy import and_
from core.taxonomy_config import dimension_aliases
from sqlalchemy import func
from ...dependencies import get_current_user
from paper_library.db import get_session
from paper_library.china_regions import CHINA_PROVINCES
from core.entity_detector import get_target_label
from core.entity_detector import load_entities_config
from paper_library.corresponding_affiliation import normalize_department
from paper_library.tag_enrichment import normalize_paradigm_selection
from core.taxonomy_config import observed_taxonomy_values
from sqlalchemy import or_
from paper_library.models import paper_authors as pa_tbl
from ...config import settings
from core.taxonomy_config import taxonomy_options

from .common import (
    COUNTRY_EN_TO_ZH,
    COUNTRY_ZH_TO_EN,
    _apply_sort,
    _build_base_query,
    _current_taxonomy,
    _database_change_token,
    _get_cached_filter_options,
    _get_cached_paper_list,
    _get_ordered_authors_for_papers,
    _impact_factor_subquery,
    _paper_to_dict,
    _parse_ids,
    _set_cached_filter_options,
    _set_cached_paper_list,
    _taxonomy_values,
)

router = APIRouter()

@router.get("/entities")
def list_entities(user: dict = Depends(get_current_user)):
    """返回可用于论文课题筛选的配置项。"""
    del user
    entities = load_entities_config().get("entities", {})
    return {
        "items": [
            str(value.get("label") or key)
            for key, value in entities.items()
            if isinstance(value, dict)
        ],
        "target_label": get_target_label(),
    }

def list_papers(
    q: Optional[str] = Query(default=None),
    journal: Optional[str] = Query(default=None),
    year_from: Optional[int] = Query(default=None),
    year_to: Optional[int] = Query(default=None),
    paper_ids: Optional[str] = Query(default=None, description="Comma-separated paper IDs, e.g. 191,494,599"),
    entity_label: Optional[str] = Query(default=None, description="Filter by configured entity label"),
    affiliation: Optional[str] = Query(default=None, description="Search by author affiliation"),
    research_field: Optional[str] = Query(default=None, description="Search by a configured research topic"),
    research_topic: Optional[str] = Query(default=None, description="Canonical research topic"),
    neuroscience_subtopic: Optional[str] = Query(default=None, description="Neuroscience subtopic"),
    country: Optional[str] = Query(default=None, description="Corresponding-author country"),
    province: Optional[str] = Query(default=None, description="Corresponding-author province"),
    city: Optional[str] = Query(default=None, description="Corresponding-author city"),
    institution_type: Optional[str] = Query(default=None, description="临床/科研"),
    department: Optional[str] = Query(default=None, description="Corresponding-author department/school"),
    processing_status: Optional[str] = Query(default=None, description="处理状态，多选逗号分隔"),
    review_outcome: Optional[str] = Query(default=None, description="审核结论，多选逗号分隔"),
    has_pdf: Optional[bool] = Query(default=None),
    metadata_complete: Optional[bool] = Query(default=None),
    tag: Optional[str] = Query(default=None, description="Filter by tag, e.g. '脑卒中'"),
    subject_disease: Optional[str] = Query(default=None, description="Canonical subject disease/group"),
    age_min: Optional[float] = Query(default=None, ge=0, le=120),
    age_max: Optional[float] = Query(default=None, ge=0, le=120),
    study_design: Optional[str] = Query(default=None),
    paradigm: Optional[str] = Query(default=None),
    has_multimodal: Optional[bool] = Query(default=None),
    multimodal_technique: Optional[str] = Query(default=None),
    multimodal_timing: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    sort_by: Optional[str] = Query(default=None),
    sort_order: str = Query(default="desc"),
    user: dict = Depends(get_current_user),
):
    ids = _parse_ids(paper_ids)
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        change_token = _database_change_token(session)
        # SQLite can cheaply invalidate on file mtime. PostgreSQL returns a
        # sentinel, and a process-local TTL would keep deleted/reviewed rows
        # visible for a minute; skip the cache there so writes are visible
        # immediately (the API may run multiple workers anyway).
        cache_enabled = change_token != (0, 0)
        cache_key = (
            q, journal, year_from, year_to, tuple(ids or ()), entity_label,
            affiliation, research_field, research_topic, neuroscience_subtopic,
            country, province, city, institution_type, department,
            processing_status, review_outcome, has_pdf, metadata_complete,
            tag, subject_disease, age_min, age_max,
            study_design, paradigm, has_multimodal, multimodal_technique,
            multimodal_timing,
            page, page_size, sort_by, sort_order,
            user.get("role") == "limited",
            change_token,
        )
        cached = _get_cached_paper_list(cache_key) if cache_enabled else None
        if cached is not None:
            return cached

        query = _build_base_query(
            session, q, journal, year_from, year_to, paper_ids=ids, entity_label=entity_label,
            affiliation=affiliation, research_field=research_field, research_topic=research_topic,
            neuroscience_subtopic=neuroscience_subtopic,
            processing_status=processing_status, review_outcome=review_outcome,
            has_pdf=has_pdf, metadata_complete=metadata_complete,
            tag=tag, country=country,
            province=province, city=city, institution_type=institution_type,
            department=department,
            subject_disease=subject_disease, age_min=age_min, age_max=age_max,
            study_design=study_design, paradigm=paradigm,
            has_multimodal=has_multimodal, multimodal_technique=multimodal_technique,
            multimodal_timing=multimodal_timing,
            restricted_role=(user.get("role") == "limited"),
            can_view_excluded=(user.get("role") in {"admin", "reviewer"}),
        )
        total = query.count()

        if_subq = _impact_factor_subquery(session)
        items_query = query.outerjoin(if_subq, Paper.journal_id == if_subq.c.journal_id)
        items_query = items_query.add_columns(if_subq.c.impact_factor, if_subq.c.jcr_quartile, if_subq.c.cas_quartile, if_subq.c.emerging_quartile)
        items_query = _apply_sort(items_query, sort_by, sort_order, if_subq)

        items = (
            items_query.offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        paper_ids_on_page = [paper.id for paper, *_ in items]
        authors_by_paper = _get_ordered_authors_for_papers(session, paper_ids_on_page)

        result = {
            "items": [
                _paper_to_dict(
                    p, if_val, jcr_val, cas_val, em_val,
                    ordered_authors=authors_by_paper.get(p.id, []),
                )
                for p, if_val, jcr_val, cas_val, em_val in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
        if cache_enabled:
            _set_cached_paper_list(cache_key, result)
        return result

@router.get("/filter-options")
def get_filter_options(
    q: Optional[str] = Query(default=None),
    entity_label: Optional[str] = Query(default=None),
    journal: Optional[str] = Query(default=None),
    year_from: Optional[int] = Query(default=None),
    year_to: Optional[int] = Query(default=None),
    country: Optional[str] = Query(default=None),
    province: Optional[str] = Query(default=None),
    city: Optional[str] = Query(default=None),
    institution_type: Optional[str] = Query(default=None),
    department: Optional[str] = Query(default=None),
    affiliation: Optional[str] = Query(default=None),
    research_field: Optional[str] = Query(default=None),
    research_topic: Optional[str] = Query(default=None),
    neuroscience_subtopic: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    subject_disease: Optional[str] = Query(default=None),
    age_min: Optional[float] = Query(default=None, ge=0, le=120),
    age_max: Optional[float] = Query(default=None, ge=0, le=120),
    study_design: Optional[str] = Query(default=None),
    paradigm: Optional[str] = Query(default=None),
    has_multimodal: Optional[bool] = Query(default=None),
    multimodal_technique: Optional[str] = Query(default=None),
    multimodal_timing: Optional[str] = Query(default=None),
    review_outcome: Optional[str] = Query(default=None),
    has_pdf: Optional[bool] = Query(default=None),
    metadata_complete: Optional[bool] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    """Return self-excluding, dynamically counted facets for current filters.

    Each facet applies every active condition except itself.  This keeps the
    selected value visible while making all other counts reflect the current
    intersection of filters.
    """

    filter_values = {
        "q": q, "entity_label": entity_label, "journal": journal,
        "year_from": year_from, "year_to": year_to,
        "country": country, "province": province, "city": city,
        "institution_type": institution_type, "department": department,
        "affiliation": affiliation, "research_field": research_field,
        "research_topic": research_topic,
        "neuroscience_subtopic": neuroscience_subtopic,
        "review_outcome": review_outcome,
        "has_pdf": has_pdf,
        "metadata_complete": metadata_complete,
        "tag": tag,
        "subject_disease": subject_disease, "age_min": age_min, "age_max": age_max,
        "study_design": study_design, "paradigm": paradigm,
        "has_multimodal": has_multimodal,
        "multimodal_technique": multimodal_technique,
        "multimodal_timing": multimodal_timing,
    }

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        taxonomy = _current_taxonomy()
        change_token = _database_change_token(session)
        cache_enabled = change_token != (0, 0)
        cache_key = (
            tuple(sorted(filter_values.items())),
            user.get("role") == "limited",
            change_token,
            repr(taxonomy),
        )
        cached = _get_cached_filter_options(cache_key) if cache_enabled else None
        if cached is not None:
            return cached

        def facet_query(*excluded: str):
            values = {key: (None if key in excluded else value) for key, value in filter_values.items()}
            return _build_base_query(
                session, paper_ids=None,
                restricted_role=(user.get("role") == "limited"),
                can_view_excluded=(user.get("role") in {"admin", "reviewer"}),
                include_excluded=(
                    user.get("role") in {"admin", "reviewer"}
                    and "review_outcome" in excluded
                ),
                **values,
            )

        def lifecycle_counts(field, *excluded: str):
            rows = (
                facet_query(*excluded)
                .with_entities(field, func.count(Paper.id))
                .group_by(field)
                .all()
            )
            return [
                {"value": value, "count": count}
                for value, count in sorted(rows, key=lambda item: str(item[0]))
                if value is not None
            ]

        review_rows = (
            facet_query("review_outcome")
            .filter(Paper.processing_status == "done")
            .with_entities(Paper.review_outcome, func.count(Paper.id))
            .group_by(Paper.review_outcome)
            .all()
        )
        lifecycle_facets = {
            "review_outcomes": [
                {"value": value, "count": count}
                for value, count in sorted(review_rows, key=lambda item: str(item[0]))
                if value is not None
            ],
            "has_pdf": lifecycle_counts(Paper.has_pdf, "has_pdf"),
            "metadata_complete": lifecycle_counts(Paper.metadata_complete, "metadata_complete"),
        }

        department_rows = (
            session.query(Author.department, Author.institution_type)
            .filter(Author.department.isnot(None), func.trim(Author.department) != "")
            .distinct().all()
        )

        def matching_departments(value: str | None) -> list[tuple[str, str | None]]:
            if not value:
                return []
            return [
                (raw, raw_type) for raw, raw_type in department_rows
                if normalize_department(raw, raw_type) == value
            ]

        def apply_department(query, value: str):
            pairs = matching_departments(value)
            if not pairs:
                return query.filter(Author.department == value)
            return query.filter(or_(*[
                and_(
                    Author.department == raw,
                    Author.institution_type == raw_type if raw_type is not None
                    else Author.institution_type.is_(None),
                )
                for raw, raw_type in pairs
            ]))

        def apply_author_context(query, excluded: str):
            if excluded != "country" and country:
                query = query.filter(Author.country == COUNTRY_ZH_TO_EN.get(country, country))
            if excluded != "province" and province:
                query = query.filter(Author.province == province)
            if excluded != "city" and city:
                query = query.filter(Author.city == city)
            if excluded != "institution_type" and institution_type:
                query = query.filter(Author.institution_type == institution_type)
            if excluded != "department" and department:
                query = apply_department(query, department)
            return query

        def author_counts(field, facet_name: str, normalize_value=None) -> dict[str, int]:
            paper_ids = facet_query(facet_name).with_entities(Paper.id)
            rows = (
                session.query(pa_tbl.c.paper_id, field, Author.institution_type)
                .join(Author, pa_tbl.c.author_id == Author.id)
                .filter(pa_tbl.c.is_corresponding.is_(True), pa_tbl.c.paper_id.in_(paper_ids))
            )
            rows = apply_author_context(rows, facet_name).distinct().all()
            counted: dict[str, set[int]] = {}
            for paper_id, raw_value, raw_type in rows:
                if raw_value is None or not str(raw_value).strip():
                    continue
                value = normalize_value(raw_value, raw_type) if normalize_value else raw_value
                if value:
                    counted.setdefault(value, set()).add(paper_id)
            return {value: len(ids) for value, ids in counted.items()}

        country_counts = author_counts(Author.country, "country")
        province_counts = author_counts(Author.province, "province")
        if country in {"中国", "China"}:
            for province_name in CHINA_PROVINCES:
                province_counts.setdefault(province_name, 0)
        city_counts = author_counts(Author.city, "city")
        institution_counts = author_counts(Author.institution_type, "institution_type")
        department_counts = author_counts(Author.department, "department", normalize_department)
        disease_rows = (
            facet_query("subject_disease")
            .with_entities(Paper.id, Paper.subject_diseases, Paper.subject_disease)
            .all()
        )
        disease_papers: dict[str, set[int]] = {}
        disease_aliases = dimension_aliases(taxonomy, "diseases")
        for paper_id, disease_values, primary_disease in disease_rows:
            values = list(disease_values) if isinstance(disease_values, list) else []
            if primary_disease:
                values.append(primary_disease)
            for value in set(observed_taxonomy_values(values, disease_aliases)):
                disease_papers.setdefault(value, set()).add(paper_id)
        disease_counts = {value: len(paper_ids) for value, paper_ids in disease_papers.items()}
        design_rows = facet_query("study_design").with_entities(Paper.id, Paper.study_design).filter(
            Paper.study_design.isnot(None), func.trim(Paper.study_design) != "",
        ).all()
        design_papers: dict[str, set[int]] = {}
        for paper_id, raw_design in design_rows:
            for value in observed_taxonomy_values(raw_design, dimension_aliases(taxonomy, "study_design")):
                design_papers.setdefault(value, set()).add(paper_id)
        design_counts = {value: len(paper_ids) for value, paper_ids in design_papers.items()}

        age_min_value, age_max_value = (
            facet_query("age_min", "age_max")
            .with_entities(func.min(Paper.subject_age_mean), func.max(Paper.subject_age_mean))
            .filter(Paper.subject_age_mean.isnot(None)).one()
        )

        topic_rows = facet_query("research_topic").with_entities(
            Paper.id, Paper.research_topics, Paper.research_fields,
        ).all()
        neuroscience_rows = facet_query("neuroscience_subtopic").with_entities(
            Paper.id, Paper.neuroscience_subtopics,
        ).all()
        paradigm_rows = facet_query("paradigm").with_entities(
            Paper.id, Paper.paradigms, Paper.paradigm,
        ).all()
        technique_rows = facet_query("multimodal_technique").with_entities(
            Paper.id, Paper.multimodal_methods, Paper.multimodal_techniques,
            Paper.multimodal_relations,
        ).all()
        relation_rows = facet_query("multimodal_timing").with_entities(
            Paper.id, Paper.multimodal_relations,
        ).all()
        multimodal_rows = facet_query("has_multimodal").with_entities(
            Paper.uses_multimodal, Paper.multimodal_techniques,
        ).all()

        topic_papers: dict[str, set[int]] = {}
        topic_aliases = dimension_aliases(taxonomy, "research_topics")
        neuroscience_papers: dict[str, set[int]] = {}
        paradigm_papers: dict[str, set[int]] = {}
        technique_papers: dict[str, set[int]] = {}
        technique_aliases = dimension_aliases(taxonomy, "multimodal")
        timing_papers: dict[str, set[int]] = {}
        multimodal_counts = {True: 0, False: 0}
        for paper_id, topic_values, legacy_topics in topic_rows:
            values = observed_taxonomy_values(topic_values, topic_aliases)
            values.extend(value for value in observed_taxonomy_values(legacy_topics, topic_aliases) if value not in values)
            for value in set(values):
                topic_papers.setdefault(value, set()).add(paper_id)
        for paper_id, paradigm_values, legacy_paradigm in paradigm_rows:
            values = normalize_paradigm_selection(paradigm_values)
            values.extend(value for value in normalize_paradigm_selection(legacy_paradigm) if value not in values)
            for value in {item.strip() for item in values if isinstance(item, str) and item.strip()}:
                paradigm_papers.setdefault(value, set()).add(paper_id)
        for scalar_value, legacy_value in multimodal_rows:
            value = scalar_value
            if not isinstance(value, bool) and isinstance(legacy_value, dict):
                value = legacy_value.get("has_multimodal")
            if isinstance(value, bool):
                multimodal_counts[value] += 1
        for paper_id, technique_values, legacy_value, technique_relations in technique_rows:
            values = observed_taxonomy_values(technique_values, technique_aliases)
            values.extend(value for value in observed_taxonomy_values(legacy_value, technique_aliases) if value not in values)
            if isinstance(technique_relations, list):
                relation_methods = [
                    relation.get("method") for relation in technique_relations
                    if isinstance(relation, dict) and isinstance(relation.get("method"), str)
                ]
                values.extend(
                    value for value in observed_taxonomy_values(relation_methods, technique_aliases)
                    if value not in values
                )
            for value in set(values):
                if multimodal_timing:
                    method_aliases = _taxonomy_values(taxonomy, "multimodal", value)
                    timing_aliases = _taxonomy_values(taxonomy, "multimodal_timing", multimodal_timing)
                    if not isinstance(technique_relations, list) or not any(
                        isinstance(relation, dict)
                        and relation.get("method") in method_aliases
                        and relation.get("timing") in timing_aliases
                        for relation in technique_relations
                    ):
                        continue
                technique_papers.setdefault(value, set()).add(paper_id)
        for paper_id, relations in relation_rows:
            if not isinstance(relations, list):
                continue
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                method = relation.get("method")
                timing = relation.get("timing")
                if multimodal_technique and method not in _taxonomy_values(taxonomy, "multimodal", multimodal_technique):
                    continue
                timing_values = observed_taxonomy_values(
                    timing,
                    dimension_aliases(taxonomy, "multimodal_timing"),
                )
                for value in timing_values:
                    timing_papers.setdefault(value, set()).add(paper_id)

        topic_counts = {value: len(ids) for value, ids in topic_papers.items()}
        for paper_id, subtopic_values in neuroscience_rows:
            if not isinstance(subtopic_values, list):
                continue
            for value in set(observed_taxonomy_values(subtopic_values, dimension_aliases(taxonomy, "neuroscience_subtopics"))):
                neuroscience_papers.setdefault(value, set()).add(paper_id)
        neuroscience_counts = {value: len(ids) for value, ids in neuroscience_papers.items()}
        paradigm_counts = {value: len(ids) for value, ids in paradigm_papers.items()}
        technique_counts = {value: len(ids) for value, ids in technique_papers.items()}
        timing_counts = {value: len(ids) for value, ids in timing_papers.items()}
        multimodal_presence_counts = {
            str(value).lower(): count for value, count in multimodal_counts.items()
        }

        tag_rows = facet_query("tag").with_entities(Paper.tags).filter(Paper.tags.isnot(None)).all()
        tag_counts: dict[str, int] = {}
        tag_aliases = dimension_aliases(taxonomy, "tags")
        tag_alias_lookup = {
            alias.casefold(): label
            for label, aliases in tag_aliases.items()
            for alias in aliases
        }
        for (paper_tags,) in tag_rows:
            if isinstance(paper_tags, list):
                for value in set(tag_value.strip() for tag_value in paper_tags if isinstance(tag_value, str) and tag_value.strip()):
                    # Normalize tag name using aliases
                    normalized_value = tag_alias_lookup.get(value.casefold(), value)
                    tag_counts[normalized_value] = tag_counts.get(normalized_value, 0) + 1

        # Keep active values available even when another condition reduces
        # their intersection to zero, so users can still see and clear them.
        if country:
            country_counts.setdefault(COUNTRY_ZH_TO_EN.get(country, country), 0)
        if province:
            province_counts.setdefault(province, 0)
        if city:
            city_counts.setdefault(city, 0)
        if institution_type:
            institution_counts.setdefault(institution_type, 0)
        if department:
            department_counts.setdefault(department, 0)
        research_options, research_groups = taxonomy_options(topic_counts, taxonomy, "research_topics")
        subtopic_options, subtopic_groups = taxonomy_options(neuroscience_counts, taxonomy, "neuroscience_subtopics")
        disease_options, disease_groups = taxonomy_options(disease_counts, taxonomy, "diseases")
        design_options, design_groups = taxonomy_options(design_counts, taxonomy, "study_design")
        paradigm_options, paradigm_groups = taxonomy_options(paradigm_counts, taxonomy, "paradigms")
        multimodal_options, multimodal_groups = taxonomy_options(technique_counts, taxonomy, "multimodal")
        multimodal_presence_options, _ = taxonomy_options(
            multimodal_presence_counts, taxonomy, "multimodal_presence",
        )
        timing_options, _ = taxonomy_options(timing_counts, taxonomy, "multimodal_timing")

        tag_options, tag_groups = taxonomy_options(tag_counts, taxonomy, "tags")

        classification_catalogs = {
            "research_topics": [option["value"] for option in research_options],
            "research_topic_groups": research_groups,
            "neuroscience_subtopics": [option["value"] for option in subtopic_options],
            "neuroscience_subtopic_groups": subtopic_groups,
            "diseases": [option["value"] for option in disease_options],
            "disease_groups": disease_groups,
            "study_designs": [option["value"] for option in design_options],
            "study_design_groups": design_groups,
            "paradigms": [option["value"] for option in paradigm_options],
            "paradigm_groups": paradigm_groups,
            "multimodal_methods": [option["value"] for option in multimodal_options],
            "multimodal_method_groups": multimodal_groups,
            "multimodal_presence": [option["value"] for option in multimodal_presence_options],
            "tags": [option["value"] for option in tag_options],
            "tag_groups": tag_groups,
            "dimension_labels": {
                str(key): str(value.get("label") or key)
                for key, value in (taxonomy.get("dimensions") or {}).items()
                if isinstance(value, dict)
            },
        }

    def counted_options(counts: dict[str, int], translate_country: bool = False, limit: int | None = None):
        items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if limit is not None:
            items = items[:limit]
        return [
            {
                "value": COUNTRY_EN_TO_ZH.get(value, value) if translate_country else value,
                "count": count,
            }
            for value, count in items
        ]

    categories = []
    for group in tag_groups:
        items = [
            {"name": value, "count": tag_counts[value], "custom": value not in tag_alias_lookup.values()}
            for value in group["options"] if value in tag_counts
        ]
        if items:
            categories.append({"name": group["label"], "tags": items})

    result = {
        **lifecycle_facets,
        "countries": counted_options(country_counts, translate_country=True),
        "provinces": counted_options(province_counts),
        "cities": counted_options(city_counts),
        "institution_types": counted_options(institution_counts),
        "departments": counted_options(department_counts, limit=200),
        "diseases": disease_options,
        "research_topics": research_options,
        "neuroscience_subtopics": subtopic_options,
        "age_range": {"min": age_min_value, "max": age_max_value},
        "study_designs": design_options,
        "paradigms": paradigm_options,
        "multimodal": multimodal_presence_options,
        "multimodal_techniques": multimodal_options,
        "multimodal_timings": timing_options,
        "tag_categories": categories,
        "classification_catalogs": classification_catalogs,
    }
    if cache_enabled:
        _set_cached_filter_options(cache_key, result)
    return result

