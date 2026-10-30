"""从 PubMed XML 提取作者机构信息中的国家。

PubMed efetch API 返回的 XML 结构中包含：
  <AuthorList>
    <Author>
      <AffiliationInfo>
        <Affiliation>Department of ..., University ..., City, Country</Affiliation>
      </AffiliationInfo>
    </Author>
  </AuthorList>

策略：
  1. 优先取最后一位作者（通讯作者惯例）的机构
  2. 降级取第一作者机构
  3. 再降级取任意有国家的机构
"""
from __future__ import annotations
import logging

import time
from typing import Optional
from defusedxml import ElementTree as ET

import requests

from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing

from core.country_extractor import extract_country_from_affiliation

logger = logging.getLogger(__name__)
NCBI_BASE_URL = api_endpoint("NCBI_BASE_URL", "ncbi_base_url")
EFETCH_URL = f"{NCBI_BASE_URL}/efetch.fcgi" if NCBI_BASE_URL else ""
PUBMED_API_KEY = configured_value("NCBI_API_KEY", "ncbi", "api_key") or None
NCBI_EMAIL = configured_value("NCBI_EMAIL", "ncbi", "email")
REQUEST_TIMEOUT = 15
RATE_LIMIT_DELAY = 0.12 if PUBMED_API_KEY else 0.34


def _extract_all_affiliations(xml_root: ET.Element) -> list[tuple[int, str]]:
    """从 PubMed XML 提取所有作者机构。返回 [(author_index, affiliation_text), ...]。

    作者索引从 1 开始，保持与 XML 中 Author 顺序一致。
    """
    results: list[tuple[int, str]] = []
    authors = xml_root.findall(".//Author")

    for idx, author in enumerate(authors, start=1):
        for aff_info in author.findall("AffiliationInfo"):
            aff_el = aff_info.find("Affiliation")
            if aff_el is not None and aff_el.text and len(aff_el.text.strip()) > 10:
                results.append((idx, aff_el.text.strip()))

    return results


def fetch_country_from_pubmed(pmid: str) -> tuple[Optional[str], Optional[str]]:
    """通过 PMID 从 PubMed XML 提取通讯作者所在国家。

    Returns (country, source_detail).
    """
    if not pmid:
        return None, None
    if not EFETCH_URL:
        warn_missing("NCBI", "NCBI_BASE_URL")
        return None, None

    pmid_clean = str(pmid).strip().rstrip(".0")  # 清理可能的 float 格式

    params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": pmid_clean,
        "api_key": PUBMED_API_KEY,
        "email": NCBI_EMAIL or None,
    }

    try:
        time.sleep(RATE_LIMIT_DELAY)
        resp = requests.get(EFETCH_URL, params=params, timeout=REQUEST_TIMEOUT, headers={"User-Agent": contact_user_agent()})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        logger.warning("PubMed affiliation lookup failed", exc_info=True)
        return None, None

    affiliations = _extract_all_affiliations(root)
    if not affiliations:
        return None, None

    # 策略 1：最后一位作者（通讯作者惯例）
    last_idx, last_aff = affiliations[-1]
    country = extract_country_from_affiliation(last_aff)
    if country:
        return country, f"pubmed_last: {last_aff[:60]}"

    # 策略 2：第一作者
    first_idx, first_aff = affiliations[0]
    country = extract_country_from_affiliation(first_aff)
    if country:
        return country, f"pubmed_first: {first_aff[:60]}"

    # 策略 3：任意作者
    for idx, aff in affiliations:
        country = extract_country_from_affiliation(aff)
        if country:
            return country, f"pubmed_any: {aff[:60]}"

    return None, None


def fetch_country_from_pubmed_by_doi(doi: str) -> tuple[Optional[str], Optional[str]]:
    """通过 DOI 解析 PMID，再提取国家。"""
    if not doi:
        return None, None

    from core.date_extractor import _resolve_pmid
    pmid = _resolve_pmid(doi)
    if not pmid:
        return None, None

    return fetch_country_from_pubmed(pmid)
