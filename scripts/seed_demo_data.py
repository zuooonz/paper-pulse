#!/usr/bin/env python3
"""为新用户写入可随时清理的虚构演示数据。"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.entity_detector import load_entities_config  # noqa: E402
from core.cli import add_db_arguments, prepare_database, setup_console  # noqa: E402
from core.taxonomy_config import dimension_items, load_taxonomy_config  # noqa: E402
from paper_library.db import get_session  # noqa: E402
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal  # noqa: E402
from paper_library.models import (  # noqa: E402
    AffiliationGeoEvidence,
    AffiliationLocation,
    ArticleDraft,
    Author,
    AuthorIdentifier,
    AuthorIdentityEvent,
    Paper,
    PaperAuthorMention,
    PublishRecord,
    draft_papers,
    paper_authors,
)
from paper_library.paper_lifecycle import (
    approve_paper,
    compute_metadata_complete,
    exclude_paper,
    refresh_pdf_flag,
)
from tools import storage


SOURCE = "demo_seed"
JOURNALS = (
    "Magnetic Resonance in Medicine",
    "NeuroImage",
    "Radiology",
    "Journal of Magnetic Resonance Imaging",
    "European Radiology",
    "Medical Image Analysis",
)

# 城市坐标为城市中心近似位置；机构、人物及研究内容均为虚构。
LOCATIONS = (
    {
        "affiliation_zh": "示例大学附属医院（演示样本）",
        "canonical_name_en": "Example University Affiliated Hospital (Demo)",
        "city": "北京市", "province": "北京市", "country": "China",
        "lat": 39.9042, "lng": 116.4074, "institution_type": "clinical",
    },
    {
        "affiliation_zh": "示例影像研究院（演示样本）",
        "canonical_name_en": "Example Institute of Imaging",
        "city": "上海市", "province": "上海市", "country": "China",
        "lat": 31.2304, "lng": 121.4737, "institution_type": "research",
    },
    {
        "affiliation_zh": "示例临床研究医院（演示样本）",
        "canonical_name_en": "Example Clinical Research Hospital (Demo)",
        "city": "广州市", "province": "广东省", "country": "China",
        "lat": 23.1291, "lng": 113.2644, "institution_type": "clinical",
    },
    {
        "affiliation_zh": "示例神经科学中心（演示样本）",
        "canonical_name_en": "Example Neuroscience Center",
        "city": "成都市", "province": "四川省", "country": "China",
        "lat": 30.5728, "lng": 104.0668, "institution_type": "research",
    },
    {
        "affiliation_zh": "示例精准医学医院（演示样本）",
        "canonical_name_en": "Example Precision Medicine Hospital",
        "city": "武汉市", "province": "湖北省", "country": "China",
        "lat": 30.5928, "lng": 114.3055, "institution_type": "clinical",
    },
    {
        "affiliation_zh": "示例生物医学工程研究院（演示样本）",
        "canonical_name_en": "Example Biomedical Engineering Institute",
        "city": "杭州市", "province": "浙江省", "country": "China",
        "lat": 30.2741, "lng": 120.1551, "institution_type": "research",
    },
    {
        "affiliation_zh": "示例伦敦影像研究中心（演示样本）",
        "canonical_name_en": "Example London Imaging Institute",
        "city": "伦敦", "province": "英格兰", "country": "United Kingdom",
        "lat": 51.5072, "lng": -0.1276, "institution_type": "research",
    },
    {
        "affiliation_zh": "示例多伦多医学影像中心（演示样本）",
        "canonical_name_en": "Example Toronto Medical Imaging Centre",
        "city": "多伦多", "province": "安大略省", "country": "Canada",
        "lat": 43.6532, "lng": -79.3832, "institution_type": "research",
    },
)

PEOPLE = (
    ("林晓雨", "Xiaoyu Lin"), ("周明远", "Mingyuan Zhou"), ("陈可欣", "Kexin Chen"),
    ("许之航", "Zhihang Xu"), ("沈予安", "Yuan Shen"), ("顾思源", "Siyuan Gu"),
    ("宋知夏", "Zhixia Song"), ("彭景然", "Jingran Peng"), ("陆清和", "Qinghe Lu"),
    ("唐星野", "Xingye Tang"), ("蒋书宁", "Shuning Jiang"), ("叶闻溪", "Wenxi Ye"),
    ("罗景澄", "Jingcheng Luo"), ("方雨桐", "Yutong Fang"), ("贺景行", "Jingxing He"),
    ("苏予希", "Yuxi Su"), ("何以宁", "Yining He"), ("江望舒", "Wangshu Jiang"),
    ("乔屿", "Yu Qiao"), ("袁心怡", "Xinyi Yuan"), ("邵言", "Yan Shao"),
    ("白明澈", "Mingche Bai"), ("程安然", "Anran Cheng"), ("谢南星", "Nanxing Xie"),
)

STUDIES = (
    ("脑组织 T1 定量图谱的重复性评估", "Repeatability of a Quantitative T1 Atlas in Brain Tissue"),
    ("多回波 T2 映射的跨设备稳定性研究", "Cross-Scanner Stability of Multi-Echo T2 Mapping"),
    ("髋关节软骨定量成像的纵向变化分析", "Longitudinal Quantitative Imaging of Hip Cartilage"),
    ("肝脏弛豫参数与组织模型的模拟比较", "A Simulated Comparison of Liver Relaxometry Models"),
    ("定量磁化率成像的重测信度分析", "Test-Retest Reliability of Quantitative Susceptibility Mapping"),
    ("脑肿瘤定量 MRI 参数的队列验证", "Validation of Quantitative MRI Measures in a Brain Tumor Cohort"),
    ("心肌 T1 映射的观察者间一致性研究", "Inter-Observer Agreement in Myocardial T1 Mapping"),
    ("多参数 qMRI 重建流程的合成数据评估", "Synthetic-Data Evaluation of a Multiparametric qMRI Pipeline"),
    ("膝关节软骨 T2* 映射的技术验证", "Technical Validation of Knee Cartilage T2* Mapping"),
    ("神经退行性疾病研究中的定量 MRI 指标", "Quantitative MRI Measures in a Neurodegeneration Study"),
    ("扩散与弛豫联合成像的模拟可重复性", "Simulated Repeatability of Joint Diffusion-Relaxometry Imaging"),
    ("定量成像参数与健康对照组的比较", "Quantitative Imaging Measures in a Healthy Control Cohort"),
)
APPROACHES = (
    ("重复性评估", "Repeatability Assessment"),
    ("跨扫描设备稳定性分析", "Cross-Scanner Stability Analysis"),
    ("纵向变化分析", "Longitudinal Change Analysis"),
    ("合成模型比较", "Synthetic Model Comparison"),
)

def _orcid_check_digit(first_fifteen: str) -> str:
    total = 0
    for digit in first_fifteen:
        total = (total + int(digit)) * 2
    result = (12 - (total % 11)) % 11
    return "X" if result == 10 else str(result)


def _demo_orcid(person_index: int) -> str | None:
    if person_index == 0:
        # ORCID 文档常见的示例格式；数据库中如已占用则跳过。
        return "0000-0002-1825-0097"
    if person_index % 3 != 0:
        return None
    first_fifteen = f"000000021825{person_index:03d}"
    digits = first_fifteen + _orcid_check_digit(first_fifteen)
    return f"{digits[:4]}-{digits[4:8]}-{digits[8:12]}-{digits[12:]}"


def _taxonomy_value(taxonomy: dict[str, Any], dimension: str, key: str, offset: int = 0) -> str:
    items = dimension_items(taxonomy, dimension)
    if not items:
        raise ValueError(f"config/taxonomy.yaml 缺少 {dimension} 配置")
    for item in items:
        if item.get("key") == key:
            return str(item.get("value") or item["label"])
    return str(items[offset % len(items)].get("value") or items[offset % len(items)]["label"])


def _entity_options(entities_config: dict[str, Any]) -> tuple[str, list[str]]:
    entities = entities_config.get("entities") or {}
    target_key = str(entities_config.get("target") or "")
    if target_key not in entities:
        raise ValueError("config/entities.yaml 未配置有效的 target 实体")
    target_label = str(entities[target_key].get("label") or target_key)
    other_labels = [
        str(entry.get("label") or key)
        for key, entry in entities.items()
        if key != target_key and isinstance(entry, dict)
    ]
    if not other_labels:
        raise ValueError("config/entities.yaml 未配置其他示例实体")
    return target_label, other_labels


def _ensure_location(session: Session, definition: dict[str, Any]) -> AffiliationLocation:
    affiliation_zh = definition["affiliation_zh"]
    location = session.scalar(
        select(AffiliationLocation).where(AffiliationLocation.affiliation_zh == affiliation_zh)
    )
    if location is not None:
        own_evidence = session.scalar(select(AffiliationGeoEvidence.id).where(
            AffiliationGeoEvidence.affiliation_location_id == location.id,
            AffiliationGeoEvidence.provider == SOURCE,
        ))
        if own_evidence is None:
            # 避免复用或改写用户已有的同名机构地理信息。
            affiliation_zh = f"{affiliation_zh}（种子副本）"
            location = session.scalar(
                select(AffiliationLocation).where(AffiliationLocation.affiliation_zh == affiliation_zh)
            )
    if location is None:
        location = AffiliationLocation(
            affiliation_zh=affiliation_zh,
            canonical_name_en=definition["canonical_name_en"],
            city=definition["city"],
            province=definition["province"],
            country=definition["country"],
            lat=definition["lat"],
            lng=definition["lng"],
        )
        session.add(location)
        session.flush()
    evidence = session.scalar(select(AffiliationGeoEvidence).where(
        AffiliationGeoEvidence.affiliation_location_id == location.id,
        AffiliationGeoEvidence.provider == SOURCE,
    ))
    if evidence is None:
        session.add(AffiliationGeoEvidence(
            affiliation_location_id=location.id,
            provider=SOURCE,
            query_text=affiliation_zh,
            institution_query=definition["canonical_name_en"],
            result_name=definition["canonical_name_en"],
            formatted_address=f"{definition['city']}, {definition['country']} (fictional demo data)",
            country=definition["country"],
            province=definition["province"],
            city=definition["city"],
            lat=definition["lat"],
            lng=definition["lng"],
            coordinate_system="WGS84",
            precision="city_center",
            provider_confidence=1.0,
            raw_response={"source": SOURCE, "fictional": True},
            audit_status="confirmed",
        ))
    return location


def _ensure_author(
    session: Session,
    person_index: int,
    locations: dict[int, AffiliationLocation],
) -> Author:
    name_zh, name = PEOPLE[person_index]
    author = session.scalar(
        select(Author)
        .join(PaperAuthorMention, PaperAuthorMention.author_id == Author.id)
        .where(
            PaperAuthorMention.source_type == SOURCE,
            Author.name == name,
            Author.name_zh == name_zh,
        )
        .limit(1)
    )
    if author is not None:
        return author

    location_index = person_index // 3
    location_definition = LOCATIONS[location_index]
    location = locations.setdefault(
        location_index,
        _ensure_location(session, location_definition),
    )
    orcid = _demo_orcid(person_index)
    if orcid and session.scalar(select(Author.id).where(Author.orcid == orcid)) is not None:
        orcid = None
    author = Author(
        name=name,
        name_zh=name_zh,
        orcid=orcid,
        affiliation=location_definition["canonical_name_en"],
        affiliation_zh=location.affiliation_zh,
        country=location.country,
        province=location.province,
        city=location.city,
        canonical_affiliation_id=location.id,
        institution_type=location_definition["institution_type"],
        department="影像医学与定量成像",
    )
    session.add(author)
    session.flush()
    return author


def _paper_lifecycle(index: int) -> dict[str, Any]:
    overrides = {
        4: {"processing_status": "done", "review_outcome": "unreviewed"},
        7: {"processing_status": "pending", "review_outcome": "unreviewed"},
        10: {"processing_status": "done", "review_outcome": "unreviewed"},
        13: {"processing_status": "processing", "review_outcome": "unreviewed"},
        16: {"processing_status": "failed", "review_outcome": "unreviewed", "processing_error": "demo_processing_failed"},
        19: {"processing_status": "done", "review_outcome": "excluded"},
        22: {"processing_status": "done", "review_outcome": "unreviewed", "metadata_complete": False},
    }
    result = {
        "processing_status": "done",
        "review_outcome": "approved",
        "has_pdf": index >= 4,
        "metadata_complete": True,
    }
    result.update(overrides.get(index, {}))
    return result


def _write_demo_pdf(paper: Paper, workspace: str | Path) -> None:
    """Create a tiny, synthetic PDF asset for demo rows that include a PDF."""
    pdf_dir = storage.get_pdf_dir(workspace)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    path = pdf_dir / f"demo_seed_{paper.id}.pdf"
    label = (paper.title or "Synthetic demo paper").encode("ascii", "replace").decode("ascii")
    label = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 36 100 Td ({label[:120]}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref_offset = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(data)
    paper.pdf_storage_path = storage.ensure_relative_path(path, workspace)
    refresh_pdf_flag(paper, workspace)


def _paper_payload(
    index: int,
    taxonomy: dict[str, Any],
    target_label: str,
    other_labels: list[str],
) -> dict[str, Any]:
    year = 2021 + index % 6
    study_zh, study_en = STUDIES[index % len(STUDIES)]
    approach_zh, approach_en = APPROACHES[index % len(APPROACHES)]
    title_zh = f"[demo] {study_zh}：{approach_zh}（队列 {index + 1:02d}）"
    title_en = f"[demo] {study_en}: {approach_en} (Cohort {index + 1:02d})"
    disease_key = ("neurodegenerative", "tumor", "cardiovascular", "musculoskeletal")[index % 4]
    disease = _taxonomy_value(taxonomy, "diseases", disease_key, index)
    topic = _taxonomy_value(taxonomy, "research_topics", "methodology" if index % 3 == 0 else disease_key, index)
    paradigm_key = ("resting_state", "task_based", "longitudinal_follow_up")[index % 3]
    design_key = ("cross_sectional", "longitudinal", "case_control", "methodological_validation")[index % 4]
    paradigm = _taxonomy_value(taxonomy, "paradigms", paradigm_key, index)
    design = _taxonomy_value(taxonomy, "study_design", design_key, index)
    is_target = index % 3 != 1
    entity_label = target_label if is_target else other_labels[index % len(other_labels)]
    tool_mention_en = "qMRLab software" if entity_label == target_label else "the hMRI toolbox for SPM"
    tool_mention_zh = "qMRLab 软件" if entity_label == target_label else "hMRI toolbox for SPM"
    abstract_en = (
        f"This study, titled ‘{title_en}’, evaluates quantitative MRI (qMRI) "
        f"measurements for {disease.lower()} using a simulated cohort of {48 + index * 3} participants. "
        f"The synthetic protocol applies {approach_en.lower()} across two invented acquisition sessions, "
        f"and names {tool_mention_en} as its example analysis software. It reports only simulated "
        "measurements, with no real patient data or clinical claims."
    )
    abstract_zh = (
        f"《{title_zh}》围绕“{disease}”评估定量磁共振成像（qMRI）指标，"
        f"设定样本量为 {48 + index * 3}。研究使用合成数据进行{approach_zh}，"
        f"并以{tool_mention_zh}作为示例分析软件名称。测量结果均为模拟数据，"
        "不含真实受试者资料，也不代表临床结论。"
    )
    return {
        "doi": f"10.5555/demo.qmri.{year}.{index + 1:03d}",
        "title": title_en,
        "title_zh": title_zh,
        "abstract": None if index == 22 else abstract_en,
        "abstract_zh": abstract_zh,
        "year": year,
        "journal_name": JOURNALS[index % len(JOURNALS)],
        **_paper_lifecycle(index),
        "is_in_domain": True,
        "cites_target": is_target,
        "entity_label": entity_label,
        "entity_source": SOURCE,
        "entity_confidence": "high",
        "equipment_brand": [entity_label],
        "research_fields": [topic],
        "research_topics": [topic],
        "subject_disease": disease,
        "subject_diseases": [disease],
        "paradigm": [paradigm],
        "paradigms": [paradigm],
        "study_design": design,
        "uses_multimodal": False,
        "multimodal_techniques": {"has_multimodal": False, "techniques": []},
        "multimodal_methods": [],
        "multimodal_relations": [],
        "keywords": ["qMRI", "quantitative MRI", approach_en.lower()],
        "keywords_zh": ["定量磁共振", disease, approach_zh],
        "tags": [],
        "extracted_images": [],
        "doi_meta": None,
        "original_data": {"source": SOURCE, "fictional": True},
        "sample_size": 48 + index * 3,
        "source": SOURCE,
    }


def _seed_authors_for_paper(
    session: Session,
    paper: Paper,
    index: int,
    locations: dict[int, AffiliationLocation],
) -> None:
    author_count = 3 + index % 4
    author_indexes = [(index * 2 + offset) % len(PEOPLE) for offset in range(author_count)]
    authors = [_ensure_author(session, person_index, locations) for person_index in author_indexes]
    corresponding = authors[-1]
    paper.corresponding_country = corresponding.country
    paper.corresponding_province = corresponding.province
    paper.corresponding_city = corresponding.city
    paper.corresponding_region = corresponding.city
    paper.corresponding_institution_type = corresponding.institution_type
    for order, author in enumerate(authors):
        is_first = order == 0
        is_corresponding = order == len(authors) - 1
        session.execute(paper_authors.insert().values(
            paper_id=paper.id,
            author_id=author.id,
            author_order=order,
            is_first=is_first,
            is_corresponding=is_corresponding,
        ))
        session.add(PaperAuthorMention(
            paper_id=paper.id,
            author_id=author.id,
            name_raw=author.name,
            name_zh=author.name_zh,
            affiliation_raw=author.affiliation,
            affiliation_zh=author.affiliation_zh,
            orcid_raw=author.orcid,
            source_type=SOURCE,
            source_ref=paper.doi,
            verification_status="unverified",
            evidence_text="Fictional author and affiliation created for demo data.",
        ))


def _seed_drafts(session: Session, papers: list[Paper]) -> None:
    draft_specs = (
        ("overview", "qMRI 演示解读：从重复性开始", "draft"),
        ("methods", "qMRI 演示解读：多参数测量怎么读", "draft"),
        ("review", "qMRI 演示专题：方法与应用", "approved"),
        ("published", "qMRI 演示简报：一条本地发布记录", "published"),
    )
    by_key: dict[str, ArticleDraft] = {}
    for draft in session.scalars(select(ArticleDraft)).all():
        meta = draft.meta if isinstance(draft.meta, dict) else {}
        if meta.get("source") == SOURCE:
            by_key[str(meta.get("seed_key"))] = draft

    for key, title, status in draft_specs:
        if key in by_key:
            continue
        now = dt.datetime.now(dt.timezone.utc)
        has_review = status in {"approved", "published"}
        content = (
            "> 完全虚构的演示文本：文中研究、数字和结论均由种子脚本编写，不对应真实论文。\n\n"
            f"## {title}\n\n"
            "定量 MRI（qMRI）把影像信号转换为可比较的参数。演示库中的合成研究展示了重复性、"
            "多参数联合分析和方法验证等常见阅读角度。阅读真实研究时，应结合样本来源、扫描协议、"
            "重测设计和不确定性报告判断结果。\n\n"
            "本段只用于查看草稿审核流程，不构成医学建议。"
        )
        draft = ArticleDraft(
            draft_type="review" if key == "review" else "single_daily",
            title=title,
            title_zh=title,
            content=content,
            status=status,
            reviewer="虚构审核员" if has_review else None,
            reviewed_at=now if has_review else None,
            review_notes="虚构演示数据；审核状态用于界面展示。" if has_review else None,
            published_at=now if status == "published" else None,
            meta={"source": SOURCE, "seed_key": key, "fictional": True},
        )
        session.add(draft)
        session.flush()
        by_key[key] = draft
        for paper in papers[: max(1, min(3, len(papers)))]:
            session.execute(draft_papers.insert().values(draft_id=draft.id, paper_id=paper.id))

def seed_demo_data(
    session: Session,
    count: int = 24,
    *,
    matcher: JcrMatcher | None = None,
    taxonomy: dict[str, Any] | None = None,
    entities_config: dict[str, Any] | None = None,
    workspace: str | Path = ROOT,
) -> dict[str, int]:
    """向现有 Session 写入演示数据；重复调用不会重复插入。"""
    if count < 1:
        raise ValueError("--count 必须大于 0")
    taxonomy = taxonomy or load_taxonomy_config(ROOT / "config")
    entities_config = entities_config or load_entities_config(config_path=ROOT / "config" / "entities.yaml")
    target_label, other_labels = _entity_options(entities_config)
    matcher = matcher or JcrMatcher(workspace=ROOT)
    if not matcher._loaded:
        matcher.load()

    existing = {
        paper.doi: paper
        for paper in session.scalars(select(Paper).where(Paper.source == SOURCE)).all()
        if paper.doi
    }
    locations: dict[int, AffiliationLocation] = {}
    created = 0
    for index in range(count):
        payload = _paper_payload(index, taxonomy, target_label, other_labels)
        paper = existing.get(payload["doi"])
        if paper is not None:
            refresh_pdf_flag(paper, workspace)
            continue
        doi_collision = session.scalar(select(Paper).where(Paper.doi == payload["doi"]))
        if doi_collision is not None:
            raise ValueError(f"DOI {payload['doi']} 已被非演示记录使用")
        paper = Paper(**payload)
        session.add(paper)
        session.flush()
        if paper.has_pdf:
            _write_demo_pdf(paper, workspace)
        journal_result = enrich_paper_journal(paper, session, matcher)
        if journal_result.get("status") != "matched":
            raise RuntimeError(f"演示期刊未能匹配参考数据：{payload['journal_name']}")
        _seed_authors_for_paper(session, paper, index, locations)
        session.flush()
        session.expire(paper, ["authors"])
        paper.metadata_complete = compute_metadata_complete(paper)
        if paper.review_outcome == "approved":
            approve_paper(paper, "demo_reviewer", "演示审核状态")
        elif paper.review_outcome == "excluded":
            exclude_paper(paper, "demo_reviewer", "演示排除状态")
        created += 1

    demo_papers = list(session.scalars(
        select(Paper).where(Paper.source == SOURCE).order_by(Paper.doi)
    ).all())
    _seed_drafts(session, demo_papers)
    session.flush()
    return {"papers_created": created, "papers_total": len(demo_papers), "drafts": 4}


def reset_demo_data(session: Session, workspace: str | Path = ROOT) -> dict[str, int]:
    """删除演示数据及其专属关联，保留其他来源的论文和账号。"""
    demo_papers = list(session.scalars(select(Paper).where(Paper.source == SOURCE)).all())
    paper_ids = [paper.id for paper in demo_papers]
    pdf_root = storage.get_pdf_dir(workspace).resolve()
    for paper in demo_papers:
        pdf_path = storage.resolve_storage_path(paper.pdf_storage_path, workspace)
        if (
            pdf_path is not None
            and pdf_path.name.startswith("demo_seed_")
            and pdf_path.resolve().is_relative_to(pdf_root)
        ):
            pdf_path.unlink(missing_ok=True)
    demo_draft_ids = []
    for draft in session.scalars(select(ArticleDraft)).all():
        meta = draft.meta if isinstance(draft.meta, dict) else {}
        if meta.get("source") == SOURCE:
            demo_draft_ids.append(draft.id)

    removed_records = 0
    if demo_draft_ids:
        removed_records = session.execute(delete(PublishRecord).where(
            PublishRecord.draft_id.in_(demo_draft_ids),
            PublishRecord.idempotency_key.like(f"{SOURCE}:%"),
        )).rowcount or 0
        session.execute(delete(draft_papers).where(draft_papers.c.draft_id.in_(demo_draft_ids)))
        # 保留任何后来由应用新增的发布记录及其草稿。
        drafts_without_external_records = [
            draft_id for draft_id in demo_draft_ids
            if session.scalar(select(PublishRecord.id).where(PublishRecord.draft_id == draft_id).limit(1)) is None
        ]
        if drafts_without_external_records:
            session.execute(delete(ArticleDraft).where(ArticleDraft.id.in_(drafts_without_external_records)))

    candidate_author_ids: set[int] = set()
    location_ids: set[int] = set()
    if paper_ids:
        candidate_author_ids.update(session.scalars(select(PaperAuthorMention.author_id).where(
            PaperAuthorMention.paper_id.in_(paper_ids),
            PaperAuthorMention.source_type == SOURCE,
        )).all())
        session.execute(delete(PaperAuthorMention).where(PaperAuthorMention.paper_id.in_(paper_ids)))
        location_ids.update(session.scalars(select(AffiliationGeoEvidence.affiliation_location_id).where(
            AffiliationGeoEvidence.provider == SOURCE,
        )).all())
        # Identifiers from later enrichment remain intact, but lose a reference to the removed demo paper.
        session.query(AuthorIdentifier).filter(AuthorIdentifier.source_paper_id.in_(paper_ids)).update(
            {AuthorIdentifier.source_paper_id: None}, synchronize_session=False,
        )
        session.execute(delete(draft_papers).where(draft_papers.c.paper_id.in_(paper_ids)))
        session.execute(delete(paper_authors).where(paper_authors.c.paper_id.in_(paper_ids)))
        session.execute(delete(Paper).where(Paper.id.in_(paper_ids)))

    removed_authors = 0
    for author_id in candidate_author_ids:
        has_papers = session.scalar(select(paper_authors.c.paper_id).where(
            paper_authors.c.author_id == author_id,
        ).limit(1))
        has_mentions = session.scalar(select(PaperAuthorMention.id).where(
            PaperAuthorMention.author_id == author_id,
        ).limit(1))
        has_identifiers = session.scalar(select(AuthorIdentifier.id).where(
            AuthorIdentifier.author_id == author_id,
        ).limit(1))
        has_identity_events = session.scalar(select(AuthorIdentityEvent.id).where(
            (AuthorIdentityEvent.source_author_id == author_id)
            | (AuthorIdentityEvent.target_author_id == author_id),
        ).limit(1))
        if not any((has_papers, has_mentions, has_identifiers, has_identity_events)):
            author = session.get(Author, author_id)
            if author is not None:
                session.delete(author)
                removed_authors += 1

    session.execute(delete(AffiliationGeoEvidence).where(AffiliationGeoEvidence.provider == SOURCE))
    removed_locations = 0
    for location_id in location_ids:
        has_authors = session.scalar(select(Author.id).where(
            Author.canonical_affiliation_id == location_id,
        ).limit(1))
        has_evidence = session.scalar(select(AffiliationGeoEvidence.id).where(
            AffiliationGeoEvidence.affiliation_location_id == location_id,
        ).limit(1))
        if not has_authors and not has_evidence:
            location = session.get(AffiliationLocation, location_id)
            if location is not None:
                session.delete(location)
                removed_locations += 1

    session.flush()
    return {
        "papers_removed": len(paper_ids),
        "drafts_removed": len(demo_draft_ids),
        "publish_records_removed": removed_records,
        "authors_removed": removed_authors,
        "locations_removed": removed_locations,
    }


def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser(description="写入虚构的 qMRI 演示数据")
    add_db_arguments(parser)
    parser.add_argument("--reset", action="store_true", help="先删除本脚本创建的数据，再重新写入")
    parser.add_argument("--count", type=int, default=24, help="演示论文数量，默认 24")
    parser.add_argument("--workspace", default=str(ROOT), help="应用工作目录")
    parser.add_argument("--config-dir", default="config", help="配置目录")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count 必须大于 0")

    prepare_database(args, workspace=args.workspace, config_dir=args.config_dir)
    workspace = Path(args.workspace).resolve()
    config_dir = Path(args.config_dir)
    if not config_dir.is_absolute():
        config_dir = workspace / config_dir
    matcher = JcrMatcher(
        config_path=config_dir / "reference.yaml",
        workspace=workspace,
    ).load()
    taxonomy = load_taxonomy_config(config_dir)
    entities_config = load_entities_config(config_path=config_dir / "entities.yaml")
    with get_session(str(workspace), str(config_dir)) as session:
        if args.reset:
            reset_stats = reset_demo_data(session)
            print(f"已清理演示数据：论文 {reset_stats['papers_removed']} 篇，草稿 {reset_stats['drafts_removed']} 篇。")
        stats = seed_demo_data(
            session,
            count=args.count,
            matcher=matcher,
            taxonomy=taxonomy,
            entities_config=entities_config,
            workspace=workspace,
        )
    print(
        f"演示数据已就绪：新增论文 {stats['papers_created']} 篇，"
        f"演示论文共 {stats['papers_total']} 篇，草稿 4 篇。"
    )


if __name__ == "__main__":
    main()
