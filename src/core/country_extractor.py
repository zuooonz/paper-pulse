"""从论文元数据中提取通讯作者单位所在国家。

数据来源优先级：
  1. doi_meta (CrossRef) — author[] 中 correspondence 标记的作者 affiliation
  2. extracted_text (PDF 全文) — "Correspondence to / Corresponding author" 行
  3. first_authors (历史录入 JSON) — affiliation 字段
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# 国家名 → 标准英文名 映射表
# 优先匹配 affiliation 字符串末尾出现的国家名
# ---------------------------------------------------------------------------

_COUNTRY_PATTERNS: list[tuple[str, str]] = [
    # (regex pattern, standard_name)
    # North America
    (r"\bUnited\s+States\s*(?:of\s+America)?\b", "USA"),
    (r"\bU\.?S\.?A\.?\b", "USA"),
    (r"\bCanada\b", "Canada"),
    (r"\bMexico\b", "Mexico"),
    # Europe
    (r"\bUnited\s+Kingdom\b", "UK"),
    (r"\bU\.?K\.?\b", "UK"),
    (r"\bEngland\b", "UK"),
    (r"\bGreat\s+Britain\b", "UK"),
    (r"\bScotland\b", "UK"),
    (r"\bGermany\b", "Germany"),
    (r"\bDeutschland\b", "Germany"),
    (r"\bFrance\b", "France"),
    (r"\bItaly\b", "Italy"),
    (r"\bItalia\b", "Italy"),
    (r"\bSpain\b", "Spain"),
    (r"\bEspaña\b", "Spain"),
    (r"\bNetherlands?\b", "Netherlands"),
    (r"\b(?:The\s+)?Netherlands\b", "Netherlands"),
    (r"\bHolland\b", "Netherlands"),
    (r"\bBelgium\b", "Belgium"),
    (r"\bBelgië\b", "Belgium"),
    (r"\bSwitzerland\b", "Switzerland"),
    (r"\bSchweiz\b", "Switzerland"),
    (r"\bSweden\b", "Sweden"),
    (r"\bSverige\b", "Sweden"),
    (r"\bNorway\b", "Norway"),
    (r"\bNorge\b", "Norway"),
    (r"\bDenmark\b", "Denmark"),
    (r"\bDanmark\b", "Denmark"),
    (r"\bFinland\b", "Finland"),
    (r"\bSuomi\b", "Finland"),
    (r"\bAustria\b", "Austria"),
    (r"\bÖsterreich\b", "Austria"),
    (r"\bPoland\b", "Poland"),
    (r"\bPolska\b", "Poland"),
    (r"\bPortugal\b", "Portugal"),
    (r"\bGreece\b", "Greece"),
    (r"\bIreland\b", "Ireland"),
    (r"\bCzech\s+Republic\b", "Czech Republic"),
    (r"\bCzechia\b", "Czech Republic"),
    (r"\bHungary\b", "Hungary"),
    (r"\bRomania\b", "Romania"),
    (r"\bCroatia\b", "Croatia"),
    (r"\bSlovenia\b", "Slovenia"),
    (r"\bSlovakia\b", "Slovakia"),
    (r"\bBulgaria\b", "Bulgaria"),
    (r"\bSerbia\b", "Serbia"),
    (r"\bUkraine\b", "Ukraine"),
    (r"\bRussia\b", "Russia"),
    (r"\bRossiya\b", "Russia"),
    # Asia
    (r"\bChina\b", "China"),
    (r"\bJapan\b", "Japan"),
    (r"\bKorea\b", "South Korea"),
    (r"\bSouth\s+Korea\b", "South Korea"),
    (r"\bRep(?:ublic|\.)\s+of\s+Korea\b", "South Korea"),
    (r"\bTaiwan\b", "Taiwan"),
    (r"\bIndia\b", "India"),
    (r"\bSingapore\b", "Singapore"),
    (r"\bThailand\b", "Thailand"),
    (r"\bMalaysia\b", "Malaysia"),
    (r"\bIndonesia\b", "Indonesia"),
    (r"\bViet\s*Nam\b", "Vietnam"),
    (r"\bVietnam\b", "Vietnam"),
    (r"\bPhilippines?\b", "Philippines"),
    (r"\bPakistan\b", "Pakistan"),
    (r"\bBangladesh\b", "Bangladesh"),
    (r"\bSri\s+Lanka\b", "Sri Lanka"),
    (r"\bIsrael\b", "Israel"),
    (r"\bSaudi\s+Arabia\b", "Saudi Arabia"),
    (r"\bUAE\b", "UAE"),
    (r"\bUnited\s+Arab\s+Emirates\b", "UAE"),
    (r"\bQatar\b", "Qatar"),
    (r"\bKuwait\b", "Kuwait"),
    (r"\bIran\b", "Iran"),
    (r"\bTurkey\b", "Turkey"),
    (r"\bTürkiye\b", "Turkey"),
    # Oceania
    (r"\bAustralia\b", "Australia"),
    (r"\bNew\s+Zealand\b", "New Zealand"),
    # South America
    (r"\bBrazil\b", "Brazil"),
    (r"\bBrasil\b", "Brazil"),
    (r"\bArgentina\b", "Argentina"),
    (r"\bChile\b", "Chile"),
    (r"\bColombia\b", "Colombia"),
    (r"\bPeru\b", "Peru"),
    # Africa
    (r"\bSouth\s+Africa\b", "South Africa"),
    (r"\bEgypt\b", "Egypt"),
    (r"\bNigeria\b", "Nigeria"),
    (r"\bKenya\b", "Kenya"),
    (r"\bEthiopia\b", "Ethiopia"),
    (r"\bMorocco\b", "Morocco"),
    (r"\bTunisia\b", "Tunisia"),
    # Special regions
    (r"\bHong\s+Kong\b", "Hong Kong"),
    (r"\bMacau\b", "Macau"),
    (r"\bMacao\b", "Macau"),
]


def extract_country_from_affiliation(affiliation: str) -> Optional[str]:
    """从机构字符串中提取国家名。

    策略：在字符串中搜索国家关键词，取位置最靠后的匹配（国家通常写在末尾）。
    """
    if not affiliation or len(affiliation.strip()) < 5:
        return None

    best_country = None
    best_end = -1

    for pattern, standard_name in _COUNTRY_PATTERNS:
        for m in re.finditer(pattern, affiliation, re.IGNORECASE):
            end = m.end()
            if end > best_end:
                best_end = end
                best_country = standard_name

    return best_country


# ---------------------------------------------------------------------------
# 1) 从 doi_meta (CrossRef) 提取
# ---------------------------------------------------------------------------

def _find_corresponding_author_affiliations(doi_meta: dict) -> list[str]:
    """从 CrossRef doi_meta 中找到通讯作者的 affiliation 列表。

    CrossRef 的 assertion 数组中可能有 name="corresponding" 的标记，
    通过 author[].{name, orcid} 匹配定位具体作者。
    """
    if not doi_meta or not isinstance(doi_meta, dict):
        return []

    msg = doi_meta.get("message", doi_meta)
    authors = msg.get("author", [])
    assertions = msg.get("assertion", [])

    if isinstance(assertions, dict):
        assertions = [assertions]

    if not isinstance(authors, list):
        return []

    # 1. 从 assertions 找 correspondence 标记
    corresponding_authors: list[dict] = []
    corr_names: set[str] = set()
    corr_orcids: set[str] = set()

    for a in assertions:
        name = str(a.get("name", "")).lower()
        if name in ("correspondence", "corresponding", "corresponding author"):
            label = str(a.get("label", ""))
            if "@" not in label:
                corr_names.add(label.lower())
            # ORCID based
            orcid = str(a.get("ORCID", ""))
            if orcid:
                corr_orcids.add(orcid)

    # 2. 匹配作者
    for author in authors:
        if not isinstance(author, dict):
            continue
        full_name = f"{author.get('given', '')} {author.get('family', '')}".strip().lower()
        orcid = str(author.get("ORCID", ""))

        is_corr = False
        if full_name and any(cn in full_name for cn in corr_names):
            is_corr = True
        if orcid and orcid in corr_orcids:
            is_corr = True

        if is_corr:
            corresponding_authors.append(author)

    # 3. 如果 assertion 未显式标记，使用 "last author" 惯例
    #    (sequence="additional" 且排在最后的作者通常是通讯)
    if not corresponding_authors and len(authors) >= 2:
        last = authors[-1]
        if isinstance(last, dict):
            corresponding_authors.append(last)

    # 4. 如果仍然为空，用 first author 作为 fallback
    if not corresponding_authors and authors:
        first = authors[0]
        if isinstance(first, dict):
            corresponding_authors.append(first)

    # 5. 提取 affiliation
    affiliations: list[str] = []
    for author in corresponding_authors:
        aff_list = author.get("affiliation", [])
        if isinstance(aff_list, dict):
            aff_list = [aff_list]
        for aff in aff_list:
            if isinstance(aff, dict) and aff.get("name"):
                affiliations.append(aff["name"])
            elif isinstance(aff, str):
                affiliations.append(aff)

    return affiliations


def extract_country_from_doi_meta(doi_meta: dict) -> tuple[Optional[str], Optional[str]]:
    """从 CrossRef doi_meta 提取通讯作者国家。Returns (country, source_detail)."""
    affiliations = _find_corresponding_author_affiliations(doi_meta)
    for aff in affiliations:
        country = extract_country_from_affiliation(aff)
        if country:
            return country, f"crossref: {aff[:80]}"

    # 降级：通讯作者无机构 → 用第一作者机构
    msg = doi_meta.get("message", doi_meta) if isinstance(doi_meta, dict) else {}
    authors = msg.get("author", [])
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            aff_list = first.get("affiliation", [])
            if isinstance(aff_list, dict):
                aff_list = [aff_list]
            for aff in aff_list:
                name = aff.get("name", "") if isinstance(aff, dict) else str(aff)
                if len(name) > 5:
                    country = extract_country_from_affiliation(name)
                    if country:
                        return country, f"crossref_1st: {name[:80]}"

    return None, None


# ---------------------------------------------------------------------------
# 2) 从 PDF 全文（extracted_text）提取
# ---------------------------------------------------------------------------

_CORR_PATTERNS = [
    re.compile(r"Correspond(?:ence|ing)(?:\s+author)?(?:\s+(?:should\s+)?be\s+(?:addressed|sent)\s+to)?[:\s]+(.{10,300})", re.IGNORECASE),
    re.compile(r"Correspondence to:\s*(.{10,300})", re.IGNORECASE),
    re.compile(r"Address for correspondence:\s*(.{10,300})", re.IGNORECASE),
]

# 首页作者块中的编号机构行: "1 Department of ..., City, Country"
_AFF_NUMBERED_RE = re.compile(
    r'(?:^|\n)\s*(\d{1,2})\s+(.+?)(?=\n\s*\d{1,2}\s+|\n\s*\n|\n(?:Abstract|A B S T R A C T|Keywords|Received|Accepted|Published|Correspondence))',
    re.MULTILINE | re.DOTALL
)

# 作者行上的上标数字: Name1, Name2,3*
_AUTHOR_SUPER_RE = re.compile(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*(\d+(?:,\d+)*)\s*[\*]?')


def extract_country_from_pdf_text(text: str | None) -> tuple[Optional[str], Optional[str]]:
    """从 PDF 全文提取通讯作者国家。Returns (country, source_detail).

    改进策略（从 author block 提取）：
    1. 找 "Correspondence to:" 行（原有逻辑）
    2. 找首页编号机构行 → 提取国家（新增：覆盖面更大）
    3. 匹配 通讯作者上标 ↔ 机构编号 → 提取通讯对应机构的国家
    """
    if not text:
        return None, None

    # Strategy 1: Correspondence line
    for pattern in _CORR_PATTERNS:
        for m in pattern.finditer(text):
            snippet = m.group(1).strip()
            country = extract_country_from_affiliation(snippet)
            if country:
                return country, f"pdf_corr: {snippet[:60]}"

    # Strategy 2: Author block numbered affiliations
    country = _extract_from_author_block(text)
    if country:
        return country, "pdf_author_block"

    return None, None


def _extract_from_author_block(text: str) -> Optional[str]:
    """从 PDF 首页作者块提取国家：识别编号机构行 → 提取国家。

    PDF 首页常见格式：
      Name1, Name2, Name3*
      1 Department of X, University Y, City, Country
      2 Institute Z, City, Country
    """
    # 只搜前 4000 字符（首页）
    header = text[:4000]

    # 找所有编号机构行
    aff_lines: list[tuple[int, str]] = []  # (number, text)
    for m in _AFF_NUMBERED_RE.finditer(header):
        num = int(m.group(1))
        content = m.group(2).strip()
        if len(content) > 10:
            aff_lines.append((num, content))

    if not aff_lines:
        return None

    # Strategy 2a: 通讯作者对应机构（高级策略）
    # 找 author line 上的上标数字 + "*" 标记
    author_line = ""
    for line in header.split('\n')[:20]:
        stripped = line.strip()
        # 作者行特征：多个逗号分隔的名字 + 数字上标
        if re.search(r'[A-Z][a-z]+.*\d+[,\d]*\s*\*?', stripped) and len(stripped) > 20:
            author_line = stripped
            break

    if author_line:
        # 找带 "*" 标记的作者（通讯作者标记）
        star_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*(\d+(?:,\d+)*)\s*\*', author_line)
        if star_match:
            # 取该通讯作者的第一个机构编号
            nums = star_match.group(2).split(',')
            corr_num = int(nums[-1])  # 最后一位通常是通讯
            for aff_num, aff_text in aff_lines:
                if aff_num == corr_num:
                    country = extract_country_from_affiliation(aff_text)
                    if country:
                        return country

    # Strategy 2b: 降级 → 从第一个机构行提取（通讯通常是第一/最后作者机构）
    for aff_num, aff_text in aff_lines:
        country = extract_country_from_affiliation(aff_text)
        if country:
            return country

    return None


# ---------------------------------------------------------------------------
# 3) 从 first_authors JSON 提取（历史数据）
# ---------------------------------------------------------------------------

def extract_country_from_first_authors(first_authors: list | None) -> tuple[Optional[str], Optional[str]]:
    """从 first_authors / corresponding_authors JSON 提取国家。"""
    if not first_authors or not isinstance(first_authors, list):
        return None, None

    for author in first_authors:
        if isinstance(author, dict):
            aff = author.get("affiliation", "")
            if aff:
                country = extract_country_from_affiliation(str(aff))
                if country:
                    return country, f"legacy: {str(aff)[:80]}"
    return None, None


# ---------------------------------------------------------------------------
# 综合提取（Paper ORM 对象）
# ---------------------------------------------------------------------------

def enrich_paper_country(paper) -> tuple[Optional[str], Optional[str]]:
    """综合提取论文通讯作者国家。返回 (country, source)。

    优先级：
      1. doi_meta (CrossRef structured data)
      2. PubMed XML (via PMID — affiliation 数据最完整)
      3. extracted_text (PDF author block)
      4. first_authors JSON (legacy)
    """
    # Source 1: CrossRef doi_meta
    if paper.doi_meta and isinstance(paper.doi_meta, dict):
        country, detail = extract_country_from_doi_meta(paper.doi_meta)
        if country:
            return country, "crossref"

    # Source 2: PubMed XML (最可靠的机构数据源)
    if paper.pmid:
        from core.pubmed_affiliation import fetch_country_from_pubmed
        country, detail = fetch_country_from_pubmed(str(paper.pmid))
        if country:
            return country, "pubmed"
    elif paper.doi:
        from core.pubmed_affiliation import fetch_country_from_pubmed_by_doi
        country, detail = fetch_country_from_pubmed_by_doi(paper.doi)
        if country:
            return country, "pubmed"

    # Source 3: PDF author block
    if paper.extracted_text:
        country, detail = extract_country_from_pdf_text(paper.extracted_text)
        if country:
            return country, "pdf"

    # Source 4: Legacy first_authors / corresponding_authors JSON
    for field_name in ("corresponding_authors", "first_authors"):
        legacy = getattr(paper, field_name, None)
        if legacy:
            country, detail = extract_country_from_first_authors(legacy)
            if country:
                return country, "legacy"

    return None, None
