"""期刊信息自动补全模块。

从配置指定的 jcr.db (ShowJCR) 加载期刊影响因子、分区等数据，
在论文导入流程中自动匹配并补全期刊信息。

表名、列名和模糊匹配阈值均由 config/reference.yaml 提供。

匹配策略（优先级）：
  1. ISSN / eISSN 精确匹配
  2. 期刊名称归一化后精确匹配
  3. 期刊名称模糊匹配 (阈值 0.85)
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from paper_library.models import Journal, JournalMetric, Paper
from paper_library.crud import upsert_journal
from core.config_loader import load_yaml_config

logger = logging.getLogger(__name__)
_WARNED_REFERENCE_ISSUES: set[tuple[str, str, str]] = set()


# ── 辅助函数 ──────────────────────────────────────────────────


def normalize_journal(name: str) -> str:
    """归一化期刊名称用于匹配。"""
    if not name or not isinstance(name, str):
        return ""
    name = name.upper().strip()
    name = re.sub(r"[^\w\s\-&]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _parse_impact_factor(val) -> Optional[float]:
    """解析影响因子，处理 '<0.1' 等格式。"""
    if val is None:
        return None
    s = str(val).strip()
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None


def _format_quartile(val, is_top: bool = False) -> str:
    """格式化分区显示，如 Q1, Q1(Top)。"""
    s = str(val).strip() if val else ""
    if not s or s.lower() in ("nan", "none", "/"):
        return ""
    m = re.search(r"([1-4])", s)
    if not m:
        return ""
    display = f"Q{m.group(1)}"
    if is_top or "(top)" in s.lower():
        display += "(Top)"
    return display


# ── JCR 数据类 ────────────────────────────────────────────────


@dataclass
class JcrEntry:
    """单条 JCR 期刊数据"""
    name: str
    issn: Optional[str] = None
    eissn: Optional[str] = None
    impact_factor: Optional[float] = None
    jcr_quartile: Optional[str] = None  # Q1/Q2/Q3/Q4
    jcr_year: int = 2025  # 影响因子所属年份
    cas_partition: Optional[str] = None  # 中科院分区
    emerging_partition: Optional[str] = None  # 新锐分区
    is_top: bool = False
    warning: Optional[str] = None


# ── JCR 匹配器 ────────────────────────────────────────────────


class JcrMatcher:
    """JCR 期刊数据加载与匹配器。

    使用方式：
        matcher = JcrMatcher()  # 一次性加载
        entry = matcher.match("Nature Neuroscience", issn="1234-5678")
    """

    def __init__(
        self,
        jcr_db_path: Optional[Path | str] = None,
        *,
        config_path: Optional[Path | str] = None,
        config: Optional[Mapping[str, Any]] = None,
        workspace: str | Path = ".",
    ):
        project_config = Path(__file__).resolve().parents[2] / "config" / "reference.yaml"
        selected_config = os.environ.get("REFERENCE_CONFIG") or config_path or project_config
        self.config_path = Path(selected_config)
        self.config = dict(config) if config is not None else load_yaml_config(self.config_path)
        self.journal_metrics_config = self.config.get("journal_metrics", {}) or {}
        configured_db_path = (
            os.environ.get("JOURNAL_METRICS_DB")
            or self.journal_metrics_config.get("path")
            or jcr_db_path
            or "data/reference/jcr.db"
        )
        self.workspace = Path(workspace).resolve()
        self.jcr_db_path = Path(configured_db_path) if configured_db_path else None
        if self.jcr_db_path is not None and not self.jcr_db_path.is_absolute():
            self.jcr_db_path = (self.workspace / self.jcr_db_path).resolve()
        try:
            self.fuzzy_threshold = float(self.journal_metrics_config.get("fuzzy_threshold", 0.85))
        except (TypeError, ValueError):
            self.fuzzy_threshold = 0.85
        self._entries: Dict[str, JcrEntry] = {}  # normalized_name -> entry
        self._issn_map: Dict[str, JcrEntry] = {}  # issn/eissn -> entry
        self._word_index: Dict[str, set] = {}     # word -> set of normalized_names
        self.jcr_year = self._year_from_jcr_config(self.journal_metrics_config)
        self._loaded = False

    @staticmethod
    def _year_from_jcr_config(config: Mapping[str, Any] | None = None) -> int:
        tables = (config or {}).get("tables", {}) or {}
        jcr = tables.get("jcr", {}) or {}
        impact_factor_column = str(jcr.get("impact_factor") or "")
        match = re.search(r"20\d{2}", impact_factor_column)
        return int(match.group()) if match else 2025

    def _warn_once(self, category: str, issue: str, message: str) -> None:
        key = (str(self.jcr_db_path or ""), category, issue)
        if key in _WARNED_REFERENCE_ISSUES:
            return
        _WARNED_REFERENCE_ISSUES.add(key)
        logger.warning("%s", message)

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _read_table_rows(
        self,
        conn: sqlite3.Connection,
        category: str,
        spec: Mapping[str, Any],
        required: list[str],
    ) -> list[dict[str, Any]] | None:
        table = str(spec.get("table") or "").strip()
        if not table:
            self._warn_once(category, "table-config", f"[JcrMatcher] 跳过 {category}：配置中未提供表名")
            return None
        actual = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)",
            (table,),
        ).fetchone()
        if not actual:
            self._warn_once(category, f"missing-table:{table}", f"[JcrMatcher] 跳过 {category}：参考库缺少表 {table}")
            return None
        table = actual[0]
        columns = {
            str(row[1]).casefold(): str(row[1])
            for row in conn.execute(f"PRAGMA table_info({self._quote_identifier(table)})")
        }
        configured_columns = {key: str(spec.get(key) or "").strip() for key in required}
        missing = [column for column in configured_columns.values() if not column or column.casefold() not in columns]
        if missing:
            self._warn_once(
                category,
                f"missing-columns:{table}:{','.join(missing)}",
                f"[JcrMatcher] 跳过 {category}：表 {table} 缺少列 {', '.join(missing)}",
            )
            return None
        resolved = {key: columns[column.casefold()] for key, column in configured_columns.items()}
        selections = ", ".join(
            f"{self._quote_identifier(column)} AS {self._quote_identifier(key)}"
            for key, column in resolved.items()
        )
        return [dict(row) for row in conn.execute(
            f"SELECT {selections} FROM {self._quote_identifier(table)}",
        ).fetchall()]

    @staticmethod
    def _clean_identifier(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip().upper()
        if cleaned in {"", "N/A", "NA", "NONE", "NULL", "-", "/"}:
            return None
        return cleaned

    @classmethod
    def _identifiers(cls, value: Any) -> list[str]:
        if value is None:
            return []
        parts = re.split(r"[;/,|]+", str(value))
        return [cleaned for part in parts if (cleaned := cls._clean_identifier(part))]

    def _entry_for(self, name: Any) -> JcrEntry | None:
        if not name:
            return None
        norm = normalize_journal(str(name))
        if not norm:
            return None
        entry = self._entries.get(norm)
        if entry is None:
            entry = JcrEntry(name=str(name), jcr_year=self.jcr_year)
            self._entries[norm] = entry
        return entry

    def _add_issn(self, entry: JcrEntry, value: Any, *, prefer_eissn: bool = False) -> None:
        identifiers = self._identifiers(value)
        if prefer_eissn and identifiers and not entry.eissn:
            entry.eissn = identifiers[0]
        elif identifiers and not entry.issn:
            entry.issn = identifiers[0]
        for identifier in identifiers:
            self._issn_map[identifier] = entry

    def _load_category(self, conn: sqlite3.Connection, category: str, spec: Mapping[str, Any]) -> None:
        if category == "jcr":
            fields = ["journal", "issn", "eissn", "impact_factor", "quartile"]
            for row in self._read_table_rows(conn, category, spec, fields) or []:
                entry = self._entry_for(row.get("journal"))
                if not entry:
                    continue
                entry.impact_factor = _parse_impact_factor(row.get("impact_factor"))
                entry.jcr_quartile = str(row.get("quartile") or "").strip() or None
                entry.jcr_year = self.jcr_year
                self._add_issn(entry, row.get("issn"))
                self._add_issn(entry, row.get("eissn"), prefer_eissn=True)
            return

        if category == "cas":
            fields = ["journal", "issn_eissn", "category", "partition", "top"]
            for row in self._read_table_rows(conn, category, spec, fields) or []:
                entry = self._entry_for(row.get("journal"))
                if not entry:
                    continue
                is_top = str(row.get("top") or "").strip().casefold() in {"是", "yes", "true", "1", "top"}
                entry.cas_partition = _format_quartile(row.get("partition"), is_top) or None
                entry.is_top = is_top
                self._add_issn(entry, row.get("issn_eissn"))
            return

        if category == "emerging":
            fields = ["journal", "issn", "eissn", "partition", "top"]
            for row in self._read_table_rows(conn, category, spec, fields) or []:
                entry = self._entry_for(row.get("journal"))
                if not entry:
                    continue
                is_top = str(row.get("top") or "").strip().casefold() in {"是", "yes", "true", "1", "top"}
                entry.emerging_partition = _format_quartile(row.get("partition"), is_top) or None
                self._add_issn(entry, row.get("issn"))
                self._add_issn(entry, row.get("eissn"), prefer_eissn=True)
            return

        if category.startswith("warning:"):
            fields = ["journal", "reason"]
            for row in self._read_table_rows(conn, category, spec, fields) or []:
                entry = self._entry_for(row.get("journal"))
                reason = str(row.get("reason") or "").strip()
                if entry and reason:
                    table = str(spec.get("table") or "")
                    year = re.search(r"(20\d{2})$", table)
                    entry.warning = f"{year.group(1) if year else table}: {reason}"

    def _build_word_index(self):
        """构建词索引加速模糊匹配。"""
        self._word_index = {}
        for norm_name in self._entries:
            for word in norm_name.split():
                if len(word) >= 3:
                    self._word_index.setdefault(word, set()).add(norm_name)

    def load(self) -> "JcrMatcher":
        """从配置指定的参考库加载指标，缺失的类别会单独跳过。"""
        if self._loaded:
            return self
        if self.jcr_db_path is None or not self.jcr_db_path.exists():
            logger.info("[JcrMatcher] 参考数据库不存在，跳过期刊指标补全: %s", self.jcr_db_path)
            self._loaded = True
            return self
        try:
            with sqlite3.connect(str(self.jcr_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                tables = self.journal_metrics_config.get("tables", {}) or {}
                if not isinstance(tables, Mapping):
                    tables = {}
                for category in ("jcr", "cas", "emerging"):
                    spec = tables.get(category)
                    if isinstance(spec, Mapping):
                        self._load_category(conn, category, spec)
                warning_specs = tables.get("warnings", []) or []
                if isinstance(warning_specs, Mapping):
                    warning_specs = [warning_specs]
                for index, spec in enumerate(warning_specs):
                    if isinstance(spec, Mapping):
                        self._load_category(conn, f"warning:{index}", spec)
        except sqlite3.Error as exc:
            self._warn_once("database", "read-error", f"[JcrMatcher] 无法读取参考数据库 {self.jcr_db_path}: {exc}")
        self._build_word_index()
        self._loaded = True
        return self

    def _fuzzy_match(self, query: str, threshold: float | None = None) -> Optional[JcrEntry]:
        """基于词索引的模糊匹配。"""
        norm_query = normalize_journal(query)
        if not norm_query:
            return None
        if norm_query in self._entries:
            return self._entries[norm_query]

        query_words = norm_query.split()
        candidates: set = set()
        for w in query_words:
            if len(w) >= 3 and w in self._word_index:
                candidates.update(self._word_index[w])

        if len(candidates) > 500:
            scored = []
            for c in candidates:
                overlap = len(set(query_words) & set(c.split()))
                if overlap > 0:
                    scored.append((overlap, c))
            scored.sort(reverse=True)
            candidates = {c for _, c in scored[:200]}

        best_score, best_name = 0.0, None
        for cand in candidates:
            score = difflib.SequenceMatcher(None, norm_query, cand).ratio()
            if score > best_score:
                best_score, best_name = score, cand

        if best_score >= (self.fuzzy_threshold if threshold is None else threshold) and best_name:
            return self._entries[best_name]
        return None

    def match(
        self,
        journal_name: Optional[str] = None,
        issn: Optional[str] = None,
        eissn: Optional[str] = None,
    ) -> Optional[JcrEntry]:
        """匹配期刊，优先 ISSN 精确匹配，其次名称模糊匹配。"""
        if not self._loaded:
            self.load()

        # 1. ISSN / eISSN 精确匹配
        for test_issn in (issn, eissn):
            if test_issn and test_issn.strip():
                test_issn = test_issn.strip().upper()
                if test_issn in self._issn_map:
                    return self._issn_map[test_issn]

        # 2. 名称匹配
        if journal_name:
            return self._fuzzy_match(journal_name)

        return None

    @property
    def stats(self) -> dict:
        return {
            "entries": len(self._entries),
            "issn_map": len(self._issn_map),
        }


# ── 数据库写入辅助 ──────────────────────────────────────────────


def _upsert_journal_metric(
    session: Session,
    journal: Journal,
    year: int,
    impact_factor: Optional[float],
    quartile: Optional[str],
    cas_quartile: Optional[str] = None,
    emerging_quartile: Optional[str] = None,
) -> JournalMetric:
    """插入或更新 JournalMetric。"""
    stmt = select(JournalMetric).where(
        JournalMetric.journal_id == journal.id,
        JournalMetric.year == year,
    )
    existing = session.scalar(stmt)
    if existing:
        if impact_factor is not None:
            existing.impact_factor = impact_factor
        if quartile is not None:
            existing.jcr_quartile = quartile
        if cas_quartile is not None:
            existing.cas_quartile = cas_quartile
        if emerging_quartile is not None:
            existing.emerging_quartile = emerging_quartile
        session.add(existing)
        session.flush()
        return existing
    metric = JournalMetric(
        journal_id=journal.id,
        year=year,
        impact_factor=impact_factor,
        jcr_quartile=quartile,
        cas_quartile=cas_quartile,
        emerging_quartile=emerging_quartile,
    )
    session.add(metric)
    session.flush()
    return metric


# ── 主入口：补全论文期刊信息 ──────────────────────────────────


def enrich_paper_journal(
    paper: Paper,
    session: Session,
    matcher: Optional[JcrMatcher] = None,
) -> dict:
    """为论文自动补全期刊信息。

    如果 paper 已有 journal_id，仅检查是否缺少 cas_quartile/emerging_quartile。
    否则尝试从 jcr.db 匹配并创建/关联 Journal。

    Returns:
        {"status": "matched"|"skipped"|"unmatched", "journal_name": str, "details": dict}
    """
    if not paper.journal_name:
        return {"status": "skipped", "reason": "no_journal_name"}

    if matcher is None:
        matcher = JcrMatcher().load()

    # 尝试从 doi_meta 中提取 ISSN
    issn = eissn = None
    if paper.doi_meta:
        issn_list = paper.doi_meta.get("ISSN", []) if isinstance(paper.doi_meta, dict) else []
        if isinstance(issn_list, list) and issn_list:
            issn = issn_list[0]

    entry = matcher.match(
        journal_name=paper.journal_name,
        issn=issn,
        eissn=eissn,
    )

    if not entry:
        return {"status": "unmatched", "journal_name": paper.journal_name}

    details = {}

    # 创建/更新 Journal 记录
    journal_data = {
        "name": entry.name,
        "issn": entry.issn,
        "eissn": entry.eissn,
    }
    journal = upsert_journal(session, journal_data)

    # 关联 paper -> journal
    if paper.journal_id is None:
        paper.journal_id = journal.id
        details["journal_id"] = journal.id

    # 写入 JournalMetric（含 IF、JCR、CAS、新锐分区）
    _upsert_journal_metric(
        session,
        journal,
        year=entry.jcr_year,
        impact_factor=entry.impact_factor,
        quartile=entry.jcr_quartile,
        cas_quartile=entry.cas_partition or None,
        emerging_quartile=entry.emerging_partition or None,
    )
    if entry.impact_factor is not None:
        details["impact_factor"] = entry.impact_factor
    if entry.jcr_quartile:
        details["jcr_quartile"] = entry.jcr_quartile
    if entry.cas_partition:
        details["cas_quartile"] = entry.cas_partition
    if entry.emerging_partition:
        details["emerging_quartile"] = entry.emerging_partition
    details["jcr_year"] = entry.jcr_year

    return {
        "status": "matched",
        "journal_name": entry.name,
        "details": details,
    }


# ── 批量补全入口 ──────────────────────────────────────────────


def enrich_all_pending_journals(
    session: Session,
    matcher: Optional[JcrMatcher] = None,
    limit: Optional[int] = None,
) -> dict:
    """批量补全所有待处理论文的期刊信息。

    处理条件：journal_name 不为空，且 journal_id 为空 或 cas_quartile 为空。
    """
    if matcher is None:
        matcher = JcrMatcher().load()

    stmt = select(Paper).where(
        Paper.journal_name.isnot(None),
    )
    if limit:
        stmt = stmt.limit(limit)

    papers = list(session.scalars(stmt).all())
    results = {"matched": 0, "unmatched": 0, "skipped": 0, "details": []}

    for paper in papers:
        # 跳过已有完整信息的
        if paper.journal_id is not None:
            metric = session.scalar(
                select(JournalMetric).where(
                    JournalMetric.journal_id == paper.journal_id,
                    JournalMetric.cas_quartile.isnot(None),
                )
            )
            if metric is not None:
                results["skipped"] += 1
                continue

        res = enrich_paper_journal(paper, session, matcher)
        if res["status"] == "matched":
            results["matched"] += 1
            results["details"].append({
                "paper_id": paper.id,
                "title": paper.title[:60] if paper.title else "",
                **res["details"],
            })
        elif res["status"] == "unmatched":
            results["unmatched"] += 1
        else:
            results["skipped"] += 1

    return results
