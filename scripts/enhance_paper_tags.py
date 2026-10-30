"""Backfill evidence-backed structured paper classifications without LLM calls."""

from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session  # noqa: E402
from paper_library.models import Paper  # noqa: E402
from paper_library.tag_enrichment import (  # noqa: E402
    classify_multimodal_methods,
    classify_paradigms,
    classify_research_topics,
    classify_subject_diseases,
    extract_study_design,
    extract_subject_age_mean,
    extract_subject_disease,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    parser.add_argument("--entity-label", help="Only enrich papers associated with one entity; defaults to all papers")
    parser.add_argument("--paper-ids", help="Comma-separated papers.id values")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without committing")
    parser.add_argument("--overwrite", action="store_true", help="Recalculate fields that already have values")
    parser.add_argument(
        "--overwrite-manual", action="store_true",
        help="Also overwrite dimensions marked as manually confirmed (normally protected)",
    )
    parser.add_argument(
        "--field", action="append",
        choices=(
            "subject_disease", "subject_diseases", "subject_age_mean", "study_design",
            "research_topics", "paradigms", "uses_multimodal", "multimodal_methods",
        ),
        help="Only recalculate this field; repeat to select multiple fields",
    )
    parser.add_argument("--workspace", default=str(ROOT), help="Workspace containing config/ and data/")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prepare_database(args, workspace=str(ROOT))
    workspace = str(Path(args.workspace).resolve())
    # runtime.yaml uses a workspace-relative SQLite URL; align process cwd so
    # this script can also be run safely from a separate git worktree.
    os.chdir(workspace)
    requested_ids = [int(value) for value in (args.paper_ids or "").split(",") if value.strip().isdigit()]
    stats: Counter[str] = Counter()

    with get_session(workspace, "config") as session:
        query = session.query(Paper)
        if args.entity_label:
            query = query.filter(Paper.entity_label == args.entity_label)
        if requested_ids:
            query = query.filter(Paper.id.in_(requested_ids))

        for paper in query.yield_per(200):
            common_context = {
                "title": paper.title,
                "title_zh": paper.title_zh,
                "abstract": paper.abstract,
                "abstract_zh": paper.abstract_zh,
                "keywords": paper.keywords,
                "keywords_zh": paper.keywords_zh,
                "tags": paper.tags,
            }
            subject_evidence = classify_subject_diseases(
                paper.population,
                paper.abstract,
                title=paper.title,
                title_zh=paper.title_zh,
                abstract_zh=paper.abstract_zh,
                keywords=paper.keywords,
                keywords_zh=paper.keywords_zh,
                tags=paper.tags,
            )
            research_evidence = classify_research_topics(
                paper.research_fields, **common_context,
            )
            paradigm_evidence = classify_paradigms(
                paper.paradigm, **common_context,
            )
            multimodal_evidence = classify_multimodal_methods(
                paper.multimodal_techniques, **common_context,
            )
            multimodal_methods = list(multimodal_evidence)
            raw_multimodal = paper.multimodal_techniques
            if multimodal_methods:
                uses_multimodal = True
            elif isinstance(raw_multimodal, dict) and isinstance(raw_multimodal.get("has_multimodal"), bool):
                uses_multimodal = raw_multimodal["has_multimodal"]
            else:
                uses_multimodal = None
            candidates = {
                "subject_disease": extract_subject_disease(paper.population, paper.abstract),
                "subject_diseases": list(subject_evidence),
                "research_topics": list(research_evidence),
                "paradigms": list(paradigm_evidence),
                "uses_multimodal": uses_multimodal,
                "multimodal_methods": multimodal_methods,
                "subject_age_mean": extract_subject_age_mean(paper.abstract),
                "study_design": extract_study_design(paper.title, paper.abstract),
            }
            all_candidates = candidates
            if args.field:
                candidates = {field: all_candidates[field] for field in args.field}
            sources = dict(paper.classification_sources) if isinstance(paper.classification_sources, dict) else {}
            dimension_fields = {
                "subject_diseases": "subject_diseases",
                "research_topics": "research_topics",
                "study_design": "study_design",
                "paradigms": "paradigms",
                "uses_multimodal": "multimodal",
                "multimodal_methods": "multimodal",
            }
            evidence_by_dimension = {
                "subject_diseases": subject_evidence,
                "research_topics": research_evidence,
                "study_design": (
                    {all_candidates["study_design"]: ["title_or_abstract"]}
                    if all_candidates["study_design"] else {}
                ),
                "paradigms": paradigm_evidence,
                "multimodal": multimodal_evidence,
            }
            changed = False
            touched_dimensions: set[str] = set()
            for field, value in candidates.items():
                dimension = dimension_fields.get(field)
                source_info = sources.get(dimension, {}) if dimension else {}
                if (
                    dimension
                    and isinstance(source_info, dict)
                    and source_info.get("source") in {"manual", "llm_review"}
                    and not args.overwrite_manual
                ):
                    stats[f"{dimension}_manual_protected"] += 1
                    continue
                current = getattr(paper, field)
                if value is not None and (args.overwrite or current is None) and value != current:
                    setattr(paper, field, value)
                    stats[field] += 1
                    changed = True
                if dimension and (args.overwrite or current is None):
                    touched_dimensions.add(dimension)
            for dimension in touched_dimensions:
                previous_source = sources.get(dimension, {})
                source_info = {
                    "source": "auto",
                    "version": 1,
                    "evidence": evidence_by_dimension[dimension],
                }
                if isinstance(previous_source, dict) and previous_source.get("llm_rejections"):
                    source_info["llm_rejections"] = previous_source["llm_rejections"]
                sources[dimension] = source_info
            if touched_dimensions and sources != paper.classification_sources:
                paper.classification_sources = sources
                stats["classification_sources"] += 1
                changed = True
            stats["papers_changed" if changed else "papers_unchanged"] += 1

        if args.dry_run:
            session.rollback()

    mode = "DRY RUN" if args.dry_run else "COMMITTED"
    print(f"[{mode}] " + ", ".join(f"{key}={value}" for key, value in sorted(stats.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
