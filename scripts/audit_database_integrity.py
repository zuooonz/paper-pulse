#!/usr/bin/env python
"""全面数据库完整性检查。

检查项：
1. 期刊 ISSN/EISSN 异常值（N/A, NA, NONE, -, Unknown 等）
2. 同一 ISSN 对应多个期刊名（重复/冲突）
3. 同一期刊名对应多个记录（重复）
4. 孤立 journal 记录（无论文关联）
5. 孤立 paper 引用（journal_id 指向不存在的 journal）
6. 论文 journal_name 与关联 journal 的 name 不一致
7. 有 journal_id 但无 metrics 的论文
8. pdf_storage_path 存在但文件不存在的记录
9. pdf_text_extracted=True 但 extracted_text 为空
10. DOI 重复/格式异常
11. 作者信息格式异常
12. 内部 ID 重复/空缺
"""
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select, func
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper, Journal, JournalMetric


def normalize_issn(val):
    if not val:
        return ""
    v = str(val).strip().upper()
    if v in ("N/A", "NA", "NONE", "NULL", "-", "UNKNOWN", ""):
        return ""
    return v


def check_journals(session):
    print("=" * 60)
    print("1. 期刊记录检查")
    print("=" * 60)

    journals = list(session.query(Journal).all())
    print(f"\n总期刊数: {len(journals)}")

    # 1.1 ISSN 异常值
    invalid_issn_patterns = ["N/A", "NA", "NONE", "NULL", "-", "UNKNOWN", ""]
    bad_issn = []
    for j in journals:
        if j.issn and str(j.issn).strip().upper() in invalid_issn_patterns:
            bad_issn.append((j.id, j.name, j.issn))
        if j.eissn and str(j.eissn).strip().upper() in invalid_issn_patterns:
            bad_issn.append((j.id, j.name, f"eissn={j.eissn}"))
    print(f"\n  ISSN/EISSN 异常值: {len(bad_issn)} 条")
    for jid, name, issn in bad_issn[:10]:
        print(f"    ID={jid}, ISSN={issn}, {name[:50]}")

    # 1.2 同一有效 ISSN 对应多个期刊名
    issn_to_journals = defaultdict(list)
    for j in journals:
        ni = normalize_issn(j.issn)
        if ni:
            issn_to_journals[ni].append(j)
        ne = normalize_issn(j.eissn)
        if ne and ne != ni:
            issn_to_journals[ne].append(j)

    conflicts = {k: v for k, v in issn_to_journals.items() if len(v) > 1}
    print(f"\n  同一 ISSN 对应多个期刊: {len(conflicts)} 组")
    for issn, jlist in list(conflicts.items())[:5]:
        names = [f"ID{j.id}:{j.name[:30]}" for j in jlist]
        print(f"    ISSN={issn}: {', '.join(names)}")

    # 1.3 同一期刊名对应多个记录
    name_to_journals = defaultdict(list)
    for j in journals:
        name_to_journals[j.name.strip().lower() if j.name else ""].append(j)

    dup_names = {k: v for k, v in name_to_journals.items() if len(v) > 1 and k}
    print(f"\n  同一名称对应多个记录: {len(dup_names)} 组")
    for name, jlist in list(dup_names.items())[:5]:
        ids = [f"ID{j.id}(ISSN={j.issn})" for j in jlist]
        print(f"    '{name}': {', '.join(ids)}")

    # 1.4 孤立期刊（无论文关联）
    journal_ids_with_papers = set(
        r[0] for r in session.query(Paper.journal_id).filter(Paper.journal_id.isnot(None)).distinct().all()
    )
    all_journal_ids = set(j.id for j in journals)
    orphan_journals = all_journal_ids - journal_ids_with_papers
    print(f"\n  孤立期刊（无论文关联）: {len(orphan_journals)} 条")
    for jid in list(orphan_journals)[:5]:
        j = session.get(Journal, jid)
        print(f"    ID={jid}, {j.name[:50] if j else '?'}")


def check_papers(session):
    print("\n" + "=" * 60)
    print("2. 论文记录检查")
    print("=" * 60)

    total = session.query(Paper).count()
    print(f"\n总论文数: {total}")

    # 2.1 孤立引用（journal_id 不存在）
    all_journal_ids = set(j.id for j in session.query(Journal).all())
    orphan_papers = []
    for p in session.query(Paper).filter(Paper.journal_id.isnot(None)).all():
        if p.journal_id not in all_journal_ids:
            orphan_papers.append((p.id, p.internal_id, p.journal_id, p.title[:50]))
    print(f"\n  孤立引用（journal_id 不存在）: {len(orphan_papers)} 条")
    for pid, iid, jid, title in orphan_papers[:5]:
        print(f"    Paper ID={pid}, internal_id={iid}, journal_id={jid}")

    # 2.2 journal_name 与关联 journal 的 name 不一致
    mismatched = []
    for p in session.query(Paper).filter(Paper.journal_id.isnot(None)).filter(Paper.journal_name.isnot(None)).all():
        j = session.get(Journal, p.journal_id)
        if j and j.name and p.journal_name:
            if j.name.strip().lower() != p.journal_name.strip().lower():
                mismatched.append((p.id, p.internal_id, p.journal_name, j.name))
    print(f"\n  journal_name 与关联期刊名不一致: {len(mismatched)} 条")
    for pid, iid, pn, jn in mismatched[:5]:
        print(f"    internal_id={iid}: Paper='{pn}' vs Journal='{jn}'")

    # 2.3 有 journal_id 但无 metrics
    no_metrics = []
    for p in session.query(Paper).filter(Paper.journal_id.isnot(None)).all():
        metric = session.scalar(
            select(JournalMetric).where(JournalMetric.journal_id == p.journal_id)
        )
        if not metric:
            no_metrics.append((p.id, p.internal_id, p.journal_name))
    print(f"\n  有 journal_id 但无 metrics: {len(no_metrics)} 条")
    for pid, iid, jn in no_metrics[:10]:
        print(f"    internal_id={iid}, {jn}")

    # 2.4 无 journal_id 但有 journal_name
    no_journal_id = list(
        session.query(Paper).filter(Paper.journal_id.is_(None)).filter(Paper.journal_name.isnot(None)).all()
    )
    print(f"\n  无 journal_id 但有 journal_name: {len(no_journal_id)} 条")
    for p in no_journal_id[:5]:
        print(f"    internal_id={p.internal_id}: {p.journal_name}")

    # 2.5 DOI 重复
    doi_counts = defaultdict(list)
    for p in session.query(Paper).filter(Paper.doi.isnot(None)).all():
        doi_counts[p.doi.lower().strip()].append(p)
    dup_dois = {k: v for k, v in doi_counts.items() if len(v) > 1}
    print(f"\n  DOI 重复: {len(dup_dois)} 组")
    for doi, papers in list(dup_dois.items())[:3]:
        ids = [f"ID{p.id}(internal={p.internal_id})" for p in papers]
        print(f"    DOI={doi}: {', '.join(ids)}")

    # 2.6 internal_id 重复
    iid_counts = defaultdict(list)
    for p in session.query(Paper).filter(Paper.internal_id.isnot(None)).all():
        iid_counts[p.internal_id].append(p)
    dup_iids = {k: v for k, v in iid_counts.items() if len(v) > 1}
    print(f"\n  internal_id 重复: {len(dup_iids)} 组")
    for iid, papers in list(dup_iids.items())[:3]:
        ids = [f"ID{p.id}" for p in papers]
        print(f"    internal_id={iid}: {', '.join(ids)}")

    # 2.7 internal_id 空缺
    max_iid = session.query(func.max(Paper.internal_id)).scalar() or 0
    all_iids = set(p.internal_id for p in session.query(Paper).filter(Paper.internal_id.isnot(None)).all())
    missing_iids = sorted(set(range(1, max_iid + 1)) - all_iids)
    print(f"\n  internal_id 空缺（1~{max_iid}）: {len(missing_iids)} 个")
    if missing_iids:
        print(f"    缺失: {missing_iids[:20]}{'...' if len(missing_iids) > 20 else ''}")


def check_pdf(session):
    print("\n" + "=" * 60)
    print("3. PDF 检查")
    print("=" * 60)

    from pathlib import Path

    missing_pdf_file = []
    pdf_empty_extract = []

    for p in session.query(Paper).filter(Paper.pdf_storage_path.isnot(None)).all():
        path = Path(p.pdf_storage_path)
        if not path.exists():
            missing_pdf_file.append((p.id, p.internal_id, str(path)))
        elif p.pdf_text_extracted and not p.extracted_text:
            pdf_empty_extract.append((p.id, p.internal_id))

    print(f"\n  pdf_storage_path 存在但文件缺失: {len(missing_pdf_file)} 条")
    for pid, iid, path in missing_pdf_file[:5]:
        print(f"    internal_id={iid}: {path[:80]}")

    print(f"\n  pdf_text_extracted=True 但 extracted_text 为空: {len(pdf_empty_extract)} 条")


def check_authors(session):
    print("\n" + "=" * 60)
    print("4. 作者信息检查")
    print("=" * 60)

    # 4.1 first_authors 格式异常
    bad_format = []
    for p in session.query(Paper).filter(Paper.first_authors.isnot(None)).all():
        fa = p.first_authors
        if not isinstance(fa, list):
            bad_format.append((p.id, p.internal_id, "first_authors not list"))
            continue
        for entry in fa:
            if not isinstance(entry, dict) or "name" not in entry:
                bad_format.append((p.id, p.internal_id, f"bad entry: {entry}"))
                break

    print(f"\n  first_authors 格式异常: {len(bad_format)} 条")
    for pid, iid, reason in bad_format[:5]:
        print(f"    internal_id={iid}: {reason}")

    # 4.2 有 corresponding_authors 但无 first_authors
    has_corr_no_first = list(
        session.query(Paper)
        .filter(Paper.corresponding_authors.isnot(None))
        .filter(Paper.first_authors.is_(None))
        .all()
    )
    print(f"\n  有通讯作者但无第一作者: {len(has_corr_no_first)} 条")

    # 4.3 无任何作者信息
    no_authors = list(
        session.query(Paper)
        .filter(Paper.first_authors.is_(None))
        .filter(Paper.corresponding_authors.is_(None))
        .all()
    )
    print(f"\n  完全无作者信息: {len(no_authors)} 条")


def check_metrics_consistency(session):
    print("\n" + "=" * 60)
    print("5. Metrics 一致性检查")
    print("=" * 60)

    # 5.1 metrics 存在但 impact_factor 为 None
    null_if = list(
        session.query(JournalMetric)
        .filter(JournalMetric.impact_factor.is_(None))
        .filter(JournalMetric.jcr_quartile.is_(None))
        .all()
    )
    print(f"\n  metrics 完全为空（IF 和分区均为空）: {len(null_if)} 条")

    # 5.2 metrics 中的 jcr_quartile 格式异常
    bad_quartile = []
    for m in session.query(JournalMetric).filter(JournalMetric.jcr_quartile.isnot(None)).all():
        q = str(m.jcr_quartile).strip()
        if not any(x in q for x in ["Q1", "Q2", "Q3", "Q4"]):
            bad_quartile.append((m.id, m.journal_id, q))
    print(f"\n  jcr_quartile 格式异常（不含 Q1-Q4）: {len(bad_quartile)} 条")
    for mid, jid, q in bad_quartile[:5]:
        print(f"    metric_id={mid}, journal_id={jid}, value={q}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    with get_session(".", "config") as session:
        check_journals(session)
        check_papers(session)
        check_pdf(session)
        check_authors(session)
        check_metrics_consistency(session)

        print("\n" + "=" * 60)
        print("检查完成")
        print("=" * 60)


if __name__ == "__main__":
    main()
