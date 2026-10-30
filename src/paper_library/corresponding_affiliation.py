"""Deterministic enrichment for corresponding-author affiliations.

The manually checked ``authors.affiliation_zh`` value is authoritative for
institution type and department. Geographic values come from the author's
canonical ``affiliation_locations`` row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from paper_library.affiliation_normalizer import infer_country


INSTITUTION_TYPES = ("临床", "科研")

_COUNTRY_MARKERS = (
    (("香港", "Hong Kong"), "Hong Kong"),
    (("澳门", "Macau", "Macao"), "Macau"),
    (("美国", "United States", "USA"), "United States"),
    (("英国",), "United Kingdom"),
    (("加拿大",), "Canada"),
    (("新加坡",), "Singapore"),
    (("韩国",), "South Korea"),
    (("印度",), "India"),
    (("俄罗斯",), "Russia"),
)
_FOREIGN_INSTITUTION_PREFIXES = (
    (("伦敦大学", "卡迪夫大学"), "United Kingdom"),
    (("麦吉尔大学",), "Canada"),
    (("南洋理工大学", "新加坡国立大学"), "Singapore"),
    (("萨拉托夫国立大学",), "Russia"),
)


@dataclass(frozen=True)
class NormalizedGeography:
    country: Optional[str]
    province: Optional[str]
    city: Optional[str]


def normalize_author_geography(
    affiliation_zh: str | None,
    affiliation_en: str | None,
    location_country: str | None,
    location_province: str | None,
    location_city: str | None,
    city_coordinates: Mapping[str, tuple[str, float, float]] | None = None,
    institution_cities: Mapping[str, str] | None = None,
) -> NormalizedGeography:
    """Normalize geography, correcting explicit foreign-affiliation mismatches."""
    checked = (affiliation_zh or "").strip()
    first_unit = re.split(r"[\n；;，,/／]", checked, maxsplit=1)[0].strip()
    hinted_country = None
    for hints, country in _COUNTRY_MARKERS:
        if any(hint.lower() in first_unit.lower() for hint in hints):
            hinted_country = country
            break
    if not hinted_country:
        for prefixes, country in _FOREIGN_INSTITUTION_PREFIXES:
            if any(first_unit.lower().startswith(prefix.lower()) for prefix in prefixes):
                hinted_country = country
                break
    hk_guangzhou = (
        hinted_country == "Hong Kong"
        and "香港科技大学" in first_unit
        and affiliation_en
        and "guangzhou" in affiliation_en.lower()
    )
    if hk_guangzhou:
        hinted_country = "China"
    if not hinted_country and first_unit:
        # Handles manually checked English affiliations such as Exeter/England.
        hinted_country = infer_country(first_unit)
    if not hinted_country and not checked and affiliation_en:
        hinted_country = infer_country(affiliation_en)

    country = hinted_country or location_country
    if not country and (location_province or location_city):
        country = "China"

    if country == "Hong Kong":
        return NormalizedGeography(country, "香港特别行政区", "香港")
    if country == "Macau":
        return NormalizedGeography(country, "澳门特别行政区", "澳门")
    if country and country != "China" and country != location_country:
        # Do not carry a historically mismatched Chinese province/city into a
        # clearly foreign affiliation.
        return NormalizedGeography(country, None, None)

    if country in (None, "China") and city_coordinates and not hk_guangzhou:
        matched_city = None
        if institution_cities:
            for institution in sorted(institution_cities, key=len, reverse=True):
                if institution in first_unit:
                    matched_city = institution_cities[institution]
                    break
        if not matched_city:
            for city_name in sorted(city_coordinates, key=len, reverse=True):
                if city_name in first_unit:
                    matched_city = city_name
                    break
        if matched_city and matched_city in city_coordinates:
            province_name = city_coordinates[matched_city][0]
            province = _province_display_name(province_name)
            city = matched_city if matched_city in {"北京", "上海", "天津", "重庆", "香港", "澳门"} else f"{matched_city}市"
            return NormalizedGeography("China", province, city)
    return NormalizedGeography(country, location_province, location_city)


def _province_display_name(value: str) -> str:
    if value in {"北京", "上海", "天津", "重庆"}:
        return value
    autonomous = {
        "广西": "广西壮族自治区", "内蒙古": "内蒙古自治区",
        "西藏": "西藏自治区", "宁夏": "宁夏回族自治区",
        "新疆": "新疆维吾尔自治区", "香港": "香港特别行政区",
        "澳门": "澳门特别行政区",
    }
    return autonomous.get(value, value if value.endswith("省") else f"{value}省")


def extract_institution_type(affiliation_zh: str | None, affiliation_en: str | None = None) -> Optional[str]:
    """Classify the checked primary affiliation as clinical or research.

    A checked affiliation can contain several collaborating units.  For Chinese
    data the first unit is authoritative; otherwise a later hospital mention
    can incorrectly turn a university author into a hospital author.
    """

    def classify(text: str) -> Optional[str]:
        lowered = text.lower()
        if any(token in text for token in ("医院", "诊所", "卫生院")) or re.search(
            r"(?:康复|精神卫生|医疗|医学)中心", text
        ) or re.search(
            r"\b(hospital|clinic|medical cent(?:er|re)|rehabilitation cent(?:er|re))\b",
            lowered,
        ):
            return "临床"
        if any(token in text for token in (
            "大学", "学院", "学校", "研究院", "研究所", "科学院", "研究中心",
            "实验室", "科研", "研究员", "公司", "集团",
        )) or re.search(
            r"\b(university|college|school|institute|academy|laborator(?:y|ies)|"
            r"research(?:er| cent(?:er|re))?|company|corporation|corp\.?|inc\.?|ltd\.?)\b",
            lowered,
        ):
            return "科研"
        return None

    if affiliation_zh:
        primary = re.split(r"[,，;；\n/&]", affiliation_zh, maxsplit=1)[0].strip()
        result = classify(primary)
        if result:
            return result

    text = (affiliation_en or affiliation_zh or "").strip()
    if not text:
        return None
    return classify(text)


_CN_DEPARTMENT_PATTERNS = (
    # Specific clinical departments before generic 院/中心 matches.
    re.compile(r"([\u4e00-\u9fff]{2,16}(?:医学科|治疗科|内科|外科|儿科|妇产科|妇科|产科|眼科|耳鼻喉科|精神科|心理科|康复科|骨科|麻醉科|保健科|护理科|科室))(?!学)"),
    re.compile(r"([\u4e00-\u9fff]{2,30}(?:学院|学部|系(?!统)))"),
)
_EN_DEPARTMENT_PATTERNS = (
    re.compile(r"\b(?:department|school|faculty|division)\s+of\s+([^,;]{2,80})", re.IGNORECASE),
    re.compile(r"\b([^,;]{2,60}?)\s+(?:department|center)\b", re.IGNORECASE),
)


def _clean_department(value: str) -> str:
    value = value.strip(" ,，;；。")
    # Prevent a greedy Chinese match from swallowing the parent institution.
    for marker in ("医院", "大学", "研究院", "研究所", "中心", "学院", "学部"):
        pos = value.rfind(marker)
        if pos >= 0 and pos + len(marker) < len(value):
            value = value[pos + len(marker):]
    if value.count("学院") > 1 and value.endswith("学院"):
        value = value.split("学院")[-2] + "学院"
    value = re.sub(r"^附属", "", value)
    return value.strip(" ,，;；。")


def extract_department(affiliation_zh: str | None, affiliation_en: str | None = None) -> Optional[str]:
    """Extract a concise department/school name from a checked affiliation."""
    candidates: list[str] = []
    if affiliation_zh:
        for pattern in _CN_DEPARTMENT_PATTERNS:
            candidates.extend(_clean_department(match) for match in pattern.findall(affiliation_zh))
    if affiliation_en:
        for pattern in _EN_DEPARTMENT_PATTERNS:
            candidates.extend(match.strip() for match in pattern.findall(affiliation_en))
    candidates = [
        value for value in candidates
        if 2 <= len(value) <= 80
        and value not in {"中国科学院", "科学院", "学院", "学部", "系", "科"}
    ]
    institution_type = extract_institution_type(affiliation_zh, affiliation_en)
    normalized_affiliation = (affiliation_zh or "").strip(" ,，;；。")
    for value in candidates:
        # A standalone institution such as “齐鲁师范学院” is not a department.
        if normalized_affiliation == value:
            continue
        normalized = normalize_department(value, institution_type)
        if normalized:
            return normalized
    return None


# Ordered, deterministic aliases used by paper-library facets.  The checked
# author affiliation/department remains untouched; this only provides a stable
# vocabulary for filtering and counts.
_CLINICAL_DEPARTMENT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("康复医学科", ("康复", "rehabilitation", "physical medicine")),
    ("神经科", ("神经内外科", "神经科")),
    ("神经内科", ("神经内科", "neurology")),
    ("神经外科", ("神经外科", "脊柱脊髓外科", "neurosurgery")),
    ("精神心理科", ("精神", "心理", "心身", "psychiatry", "mental health")),
    ("儿科", ("儿科", "新生儿", "儿童", "发育行为", "pediatrics", "paediatrics")),
    ("耳鼻咽喉头颈外科", ("耳鼻", "头颈外科", "otolaryngology")),
    ("运动医学科", ("运动医学", "sports medicine")),
    ("老年医学科", ("老年医学", "geriatrics")),
    ("眼科", ("眼科", "ophthalmology")),
    ("妇产科", ("妇产", "妇科", "产科", "obstetrics", "gynecology", "gynaecology")),
    ("睡眠医学科", ("睡眠", "sleep medicine")),
    ("重症医学科", ("重症", "critical care", "intensive care")),
    ("作业治疗科", ("作业治疗", "occupational therapy")),
    ("护理科", ("护理", "nursing")),
    ("麻醉科", ("麻醉", "anesthes", "anaesthes")),
    ("医学影像科", ("影像", "放射", "radiology", "imaging")),
    ("中医针灸科", ("中医", "针灸", "推拿", "acupuncture")),
)

_ACADEMIC_DEPARTMENT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("心理学", ("心理", "认知科学", "psychology", "cognitive science")),
    ("体育与运动科学", ("体育", "运动科学", "运动与健康", "physical education", "sport science")),
    ("康复医学与治疗", ("康复", "rehabilitation")),
    ("生物医学工程", ("生物医学工程", "医学工程", "biomedical engineering")),
    ("机械与机电工程", ("机械", "机电", "mechanical", "mechatronic")),
    ("电子信息与自动化", ("电子", "信息工程", "信息科学", "自动化", "电气", "仪器", "光电", "electrical", "information engineering", "automation")),
    ("计算机与人工智能", ("计算机", "软件", "人工智能", "数据科学", "computer", "artificial intelligence")),
    ("医学与生命科学", ("医学院", "医学部", "临床医学院", "生命科学", "生物科学", "medicine", "life science")),
    ("临床医学", ("临床", "神经内外科", "神经内科", "神经外科", "眼科", "精神科", "clinical medicine")),
    ("教育学", ("教育", "education")),
    ("公共卫生", ("公共卫生", "循证医学", "社会医学", "public health")),
    ("交通运输与物流", ("交通", "物流", "transportation", "logistics")),
    ("设计与艺术", ("设计", "艺术", "design", "art")),
    ("语言与传播", ("语言", "传播", "外国语", "linguistic", "communication")),
    ("材料与化学工程", ("材料", "化学", "化工", "material", "chem")),
    ("工业与管理工程", ("工业工程", "系统工程", "管理工程", "建设管理", "industrial engineering")),
    ("土木与建筑工程", ("土木", "建筑", "civil engineering", "construction")),
    ("物理与基础科学", ("物理", "数学", "基础科学", "physics")),
    ("食品与生物工程", ("食品", "生物工程", "food science", "biotechnology")),
    ("中医药学", ("中医", "针灸", "推拿", "中药", "traditional chinese medicine")),
)


def _is_clinical_type(value: str | None) -> bool:
    """Accept legacy values while a database is being migrated."""
    return value in {"临床", "医院"}


def normalize_department(value: str | None, institution_type: str | None = None) -> Optional[str]:
    """Map a free-form department/school to a stable display category.

    Institution type determines whether an ambiguous term such as ``心理`` is
    presented as a clinical department or an academic discipline.  Unmatched
    values are preserved so that no manually checked information is discarded.
    """
    if not value or not value.strip():
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" ,，;；。")
    clinical_labels = {label for label, _ in _CLINICAL_DEPARTMENT_ALIASES}
    academic_labels = {label for label, _ in _ACADEMIC_DEPARTMENT_ALIASES}
    clinical_type = _is_clinical_type(institution_type)
    if cleaned in clinical_labels:
        return cleaned if clinical_type else "临床医学"
    if cleaned in academic_labels:
        return None if clinical_type else cleaned
    lowered = cleaned.casefold()
    if clinical_type:
        academic_markers = (
            "学院", "学系", "学部", "大学", "研究院", "研究所", "科学院",
            "school", "faculty", "college", "institute", "academy",
            "life science", "information science", "engineering",
        )
        if any(marker in lowered for marker in academic_markers):
            return None
    catalog = _CLINICAL_DEPARTMENT_ALIASES if clinical_type else _ACADEMIC_DEPARTMENT_ALIASES
    for label, aliases in catalog:
        if any(alias.casefold() in lowered for alias in aliases):
            return label
    if clinical_type and ("医院" in cleaned or "hospital" in lowered):
        return None
    if clinical_type and not (
        re.search(r"科$|科室$|中心$|治疗$", cleaned)
        or re.search(r"\b(department|therapy|neurology|psychiatry|nursing)\b", lowered)
    ):
        return None
    # Facets use a closed vocabulary. The checked source remains available in
    # affiliation_zh, while unmatched fragments stay out of filter options.
    return None


@dataclass(frozen=True)
class CorrespondingAffiliation:
    author_id: int
    author_order: int
    affiliation_zh: Optional[str]
    affiliation_en: Optional[str]
    country: Optional[str]
    province: Optional[str]
    city: Optional[str]


def choose_primary_affiliation(rows: Iterable[CorrespondingAffiliation]) -> Optional[CorrespondingAffiliation]:
    """Choose a stable paper-level projection while retaining relational filters.

    Papers may have several corresponding authors. Filtering uses every
    corresponding author; the paper columns are only a deterministic summary.
    Prefer a row with canonical geography, then the original author order.
    """
    values = list(rows)
    if not values:
        return None
    return min(
        values,
        key=lambda row: (
            not bool(row.country or row.province or row.city),
            not bool(row.affiliation_zh),
            row.author_order,
            row.author_id,
        ),
    )
