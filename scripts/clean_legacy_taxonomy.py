"""Report or remove paper classification values outside the active taxonomy."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.taxonomy_config import (  # noqa: E402
    canonical_taxonomy_value,
    load_taxonomy_config,
)
from paper_library.db import get_session, load_database_url  # noqa: E402
from paper_library.json_utils import decode_json  # noqa: E402
from paper_library.models import Paper  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


COLUMN_DIMENSIONS = {
    "tags": "tags",
    "research_topics": "research_topics",
    "research_fields": "research_topics",
    "neuroscience_subtopics": "neuroscience_subtopics",
    "paradigms": "paradigms",
    "paradigm": "paradigms",
    "subject_diseases": "diseases",
    "subject_disease": "diseases",
    "study_design": "study_design",
    "multimodal_methods": "multimodal",
    "multimodal_techniques": "multimodal",
}
ARRAY_COLUMNS = {
    "tags", "research_topics", "research_fields", "neuroscience_subtopics",
    "paradigms", "paradigm", "subject_diseases", "multimodal_methods",
}


def _parsed(value: Any) -> Any:
    return decode_json(value, value) if isinstance(value, str) else value


def _clean_values(
    values: Any,
    dimension: str,
    taxonomy: dict[str, Any],
    *,
    keep_custom: bool,
) -> tuple[list[str], int]:
    parsed = _parsed(values)
    source = parsed if isinstance(parsed, list) else [parsed]
    cleaned: list[str] = []
    removed = 0
    for raw in source:
        if not isinstance(raw, str) or not raw.strip():
            if raw not in (None, ""):
                removed += 1
            continue
        value = raw.strip()
        canonical = canonical_taxonomy_value(taxonomy, dimension, value)
        if canonical is not None:
            result = canonical
        elif keep_custom:
            result = value
        else:
            removed += 1
            continue
        if result not in cleaned:
            cleaned.append(result)
        else:
            removed += 1
    return cleaned, removed


def _clean_technique_values(
    source: Any,
    taxonomy: dict[str, Any],
    *,
    keep_custom: bool,
) -> tuple[list[Any], int]:
    if isinstance(source, str):
        source = [source]
    if not isinstance(source, list):
        return [], 1 if source not in (None, "") else 0
    cleaned: list[Any] = []
    removed = 0
    for item in source:
        if isinstance(item, dict):
            name_key = "name" if item.get("name") else "method" if item.get("method") else None
            raw_name = item.get(name_key) if name_key else None
            canonical = canonical_taxonomy_value(taxonomy, "multimodal", raw_name)
            result = canonical or (str(raw_name).strip() if keep_custom and raw_name else None)
            if result:
                retained = dict(item)
                retained[name_key] = result
                cleaned.append(retained)
            else:
                removed += 1
        elif isinstance(item, str):
            values, item_removed = _clean_values(
                [item], "multimodal", taxonomy, keep_custom=keep_custom,
            )
            cleaned.extend(values)
            removed += item_removed
        else:
            removed += 1
    return cleaned, removed


def clean_column_value(
    column: str,
    value: Any,
    taxonomy: dict[str, Any],
    *,
    keep_custom: bool = False,
) -> tuple[Any, int]:
    """Clean one classification column value and report removed value count."""
    if value is None:
        return None, 0
    if column == "classification_sources":
        parsed = _parsed(value)
        if not isinstance(parsed, dict):
            return None, 1
        source_dimensions = {
            "tags": "tags",
            "research_topics": "research_topics",
            "neuroscience_subtopics": "neuroscience_subtopics",
            "paradigms": "paradigms",
            "subject_diseases": "diseases",
            "study_design": "study_design",
            "multimodal": "multimodal",
        }
        retained = {}
        removed = 0
        for dimension, raw_detail in parsed.items():
            if dimension not in source_dimensions or not isinstance(raw_detail, dict):
                removed += 1
                continue
            item_dimension = source_dimensions[dimension]
            detail = dict(raw_detail)
            evidence = detail.get("evidence")
            if isinstance(evidence, dict):
                clean_evidence = {}
                for raw_value, evidence_data in evidence.items():
                    canonical = canonical_taxonomy_value(taxonomy, item_dimension, raw_value)
                    normalized = canonical or (raw_value if keep_custom else None)
                    if normalized is None:
                        removed += 1
                        continue
                    clean_evidence[normalized] = evidence_data
                detail["evidence"] = clean_evidence
            for list_key in ("llm_reviews", "llm_rejections"):
                entries = detail.get(list_key)
                if not isinstance(entries, list):
                    continue
                clean_entries = []
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
                        removed += 1
                        continue
                    canonical = canonical_taxonomy_value(taxonomy, item_dimension, entry["value"])
                    normalized = canonical or (entry["value"] if keep_custom else None)
                    if normalized is None:
                        removed += 1
                        continue
                    clean_entries.append({**entry, "value": normalized})
                detail[list_key] = clean_entries
            retained[dimension] = detail
        return retained or None, removed

    if column == "multimodal_relations":
        parsed = _parsed(value)
        if not isinstance(parsed, list):
            return [], 1
        cleaned = []
        removed = 0
        for relation in parsed:
            if not isinstance(relation, dict):
                removed += 1
                continue
            method, timing = relation.get("method"), relation.get("timing")
            canonical_method = canonical_taxonomy_value(taxonomy, "multimodal", method)
            method_value = canonical_method or (str(method).strip() if keep_custom and method else None)
            canonical_timing = canonical_taxonomy_value(taxonomy, "multimodal_timing", timing)
            timing_value = canonical_timing or (
                timing if keep_custom and isinstance(timing, str) and timing.strip() else None
            )
            if not method_value:
                removed += 1
            if not timing_value:
                removed += 1
            if method_value and timing_value:
                cleaned.append({"method": method_value, "timing": timing_value})
        return cleaned, removed

    if column == "multimodal_techniques":
        parsed = _parsed(value)
        if isinstance(parsed, dict):
            cleaned, removed = _clean_technique_values(
                parsed.get("techniques", []), taxonomy, keep_custom=keep_custom,
            )
            result = dict(parsed)
            result["techniques"] = cleaned
            return result, removed
        if isinstance(parsed, list) and any(isinstance(item, dict) for item in parsed):
            return _clean_technique_values(parsed, taxonomy, keep_custom=keep_custom)

    dimension = COLUMN_DIMENSIONS.get(column)
    if not dimension:
        return value, 0
    values, removed = _clean_values(value, dimension, taxonomy, keep_custom=keep_custom)
    if column in ARRAY_COLUMNS:
        return values, removed
    if not values:
        return None, removed
    return values[0], removed + max(0, len(values) - 1)


def clean_session(
    session,
    taxonomy: dict[str, Any],
    *,
    apply: bool = False,
    keep_custom: bool = False,
) -> dict[str, int]:
    """Clean all supported classification fields in one session."""
    stats: Counter[str] = Counter()
    for paper in session.query(Paper).yield_per(200):
        paper_changed = False
        for column in (*COLUMN_DIMENSIONS, "multimodal_relations", "classification_sources"):
            value = getattr(paper, column, None)
            cleaned, removed = clean_column_value(
                column, value, taxonomy, keep_custom=keep_custom,
            )
            stats[column] += removed
            if cleaned != value:
                paper_changed = True
                if apply:
                    setattr(paper, column, cleaned)
        if paper_changed:
            stats["rows_changed"] += 1
    if apply:
        session.commit()
    else:
        session.rollback()
    return dict(stats)


def _existing_sqlite_database(workspace: Path, config_dir: str) -> bool:
    url = make_url(load_database_url(str(workspace), config_dir))
    if not url.drivername.startswith("sqlite") or url.database in (None, ":memory:"):
        return True
    path = Path(url.database)
    if not path.is_absolute():
        path = workspace / path
    return path.is_file()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write cleaned values; default is a dry run")
    parser.add_argument("--keep-custom", action="store_true", help="Keep unconfigured values as custom data")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    if not _existing_sqlite_database(workspace, args.config_dir):
        print("未找到现有 SQLite 数据库；未执行修改。")
        return 1
    config_path = Path(args.config_dir)
    if not config_path.is_absolute():
        config_path = workspace / config_path
    taxonomy = load_taxonomy_config(config_path)
    with get_session(str(workspace), args.config_dir) as session:
        stats = clean_session(session, taxonomy, apply=args.apply, keep_custom=args.keep_custom)
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"keep_custom={args.keep_custom}")
    for column in (*COLUMN_DIMENSIONS, "multimodal_relations", "classification_sources"):
        print(f"{column}: removed={stats.get(column, 0)}")
    print(f"rows_changed={stats.get('rows_changed', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
