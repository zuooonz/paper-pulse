"""Extract accepted_date and published_date from three sources:
1. PubMed API (via PMID -> EFetch XML)
2. Crossref metadata (from existing doi_meta or fresh API call)
3. PDF full-text regex matching
"""
from __future__ import annotations
import logging

import datetime
import re
from typing import Optional

import requests
from defusedxml import ElementTree as ET

from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing

logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Date result type: (date, precision) where precision is 'year', 'month', or 'day'
# ---------------------------------------------------------------------------
DateResult = tuple[Optional[datetime.date], str]


# ---------------------------------------------------------------------------
# Month parsing utilities
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _parse_month(month_str: str) -> Optional[int]:
    if not month_str:
        return None
    m = month_str.strip().lower().rstrip(".")
    return _MONTH_MAP.get(m)


# ---------------------------------------------------------------------------
# Generic date parsing from strings
# ---------------------------------------------------------------------------

def _parse_date_str(val: str) -> DateResult:
    """Parse a generic date string. Returns (date, precision)."""
    if not val:
        return None, ""
    val = str(val).strip()

    # ISO: YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", val)
    if m:
        try:
            return datetime.date(int(m[1]), int(m[2]), int(m[3])), "day"
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_date_str）', exc_info=True)
            pass

    # Day Month Year: 20 July 2018
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", val, re.IGNORECASE)
    if m:
        try:
            day = int(m[1])
            month = _parse_month(m[2])
            year = int(m[3])
            if month:
                return datetime.date(year, month, day), "day"
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_date_str）', exc_info=True)
            pass

    # Month Day, Year: July 20, 2018
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", val, re.IGNORECASE)
    if m:
        try:
            month = _parse_month(m[1])
            day = int(m[2])
            year = int(m[3])
            if month:
                return datetime.date(year, month, day), "day"
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_date_str）', exc_info=True)
            pass

    # Month Year: July 2018
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", val, re.IGNORECASE)
    if m:
        try:
            month = _parse_month(m[1])
            year = int(m[2])
            if month:
                return datetime.date(year, month, 1), "month"
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_date_str）', exc_info=True)
            pass

    return None, ""


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _make_pdf_pattern(label: str) -> list[re.Pattern]:
    """Build regex patterns for a date label in PDF text."""
    return [
        re.compile(rf"{label}[:\s]+(\d{{4}})[-/](\d{{1,2}})[-/](\d{{1,2}})", re.IGNORECASE),
        re.compile(rf"{label}[:\s]+(\d{{1,2}})\s+([A-Za-z]+)\.?\s+(\d{{4}})", re.IGNORECASE),
        re.compile(rf"{label}[:\s]+([A-Za-z]+)\.?\s+(\d{{1,2}}),?\s+(\d{{4}})", re.IGNORECASE),
        re.compile(rf"{label}[:\s]+([A-Za-z]+)\.?\s+(\d{{4}})", re.IGNORECASE),
    ]


def _extract_pdf_date(text: str | None, label: str) -> DateResult:
    """Extract a specific date label from PDF text. Returns (date, precision)."""
    if not text:
        return None, ""

    patterns = _make_pdf_pattern(label)
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            d, precision = _parse_date_str(" ".join(match.groups()))
            if d:
                return d, precision

    # Fallback: scan lines starting with the label
    line_re = re.compile(rf"^{label}[:\s]+(.{{5,60}})$", re.IGNORECASE | re.MULTILINE)
    year_re = re.compile(r"\b(19\d{{2}}|20\d{{2}})\b")
    for line_match in line_re.finditer(text):
        line = line_match.group(1)
        year_match = year_re.search(line)
        if year_match:
            year = int(year_match.group(1))
            dm_match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\b", line, re.IGNORECASE) or \
                       re.search(r"([A-Za-z]+)\b\.?\s+(\d{1,2})", line, re.IGNORECASE)
            if dm_match:
                a, b = dm_match.groups()
                if a.isdigit():
                    day, month_str = int(a), b
                else:
                    month_str, day = a, int(b)
                month = _parse_month(month_str)
                if month:
                    try:
                        return datetime.date(year, month, day), "day"
                    except Exception:
                        logger.warning('处理失败，已跳过该项（_extract_pdf_date）', exc_info=True)
                        pass
            try:
                return datetime.date(year, 1, 1), "year"
            except Exception:
                logger.warning('处理失败，已跳过该项（_extract_pdf_date）', exc_info=True)
                pass

    return None, ""


def extract_accepted_from_pdf(text: str | None) -> DateResult:
    return _extract_pdf_date(text, "Accepted")


def extract_published_from_pdf(text: str | None) -> DateResult:
    # Try "Published online" first (more specific), then "Published"
    d = _extract_pdf_date(text, "Published online")
    if d[0]:
        return d
    return _extract_pdf_date(text, "Published")


# ---------------------------------------------------------------------------
# PubMed API extraction
# ---------------------------------------------------------------------------

NCBI_BASE_URL = api_endpoint("NCBI_BASE_URL", "ncbi_base_url")
ESEARCH_URL = f"{NCBI_BASE_URL}/esearch.fcgi" if NCBI_BASE_URL else ""
EFETCH_URL = f"{NCBI_BASE_URL}/efetch.fcgi" if NCBI_BASE_URL else ""
PUBMED_API_KEY = configured_value("NCBI_API_KEY", "ncbi", "api_key") or None
NCBI_EMAIL = configured_value("NCBI_EMAIL", "ncbi", "email")
REQUEST_TIMEOUT = 15


def _parse_pubmed_date_element(year_el, month_el, day_el) -> DateResult:
    """Parse PubMed XML date elements.
    
    Returns (date, precision). 
    - Year only -> precision 'year' (date filled as YYYY-01-01)
    - Year+Month -> precision 'month' (date filled as YYYY-MM-01)
    - Year+Month+Day -> precision 'day'
    """
    try:
        year = int(year_el.text) if year_el is not None and year_el.text else None
        if not year:
            return None, ""
        
        if month_el is None or not month_el.text:
            return datetime.date(year, 1, 1), "year"
        
        month_text = month_el.text.strip()
        if month_text.isdigit():
            month = int(month_text)
        else:
            month = _parse_month(month_text)
        if not month:
            return datetime.date(year, 1, 1), "year"
        
        if day_el is not None and day_el.text:
            day_text = day_el.text.strip()
            if day_text.isdigit():
                day = int(day_text)
                return datetime.date(year, month, day), "day"
        
        return datetime.date(year, month, 1), "month"
    except Exception:
        logger.debug("PubMed date parsing failed", exc_info=True)
        return None, ""


def _fetch_pubmed_dates(pmid: str) -> dict[str, DateResult]:
    """Fetch accepted and published dates from PubMed XML."""
    result: dict[str, DateResult] = {"accepted": (None, ""), "published": (None, "")}
    if not pmid:
        return result
    if not EFETCH_URL:
        warn_missing("NCBI", "NCBI_BASE_URL")
        return result
    params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": str(pmid),
        "api_key": PUBMED_API_KEY,
        "email": NCBI_EMAIL or None,
    }
    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=REQUEST_TIMEOUT, headers={"User-Agent": contact_user_agent()})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        # Accepted from History
        for hist in root.findall(".//PubMedPubDate"):
            status = hist.get("PubStatus", "").lower()
            if status == "accepted":
                result["accepted"] = _parse_pubmed_date_element(
                    hist.find("Year"), hist.find("Month"), hist.find("Day")
                )

        # Published date from Journal PubDate
        pub_date = root.find(".//Journal//PubDate")
        if pub_date is not None:
            year_el = pub_date.find("Year")
            month_el = pub_date.find("Month")
            day_el = pub_date.find("Day")
            result["published"] = _parse_pubmed_date_element(year_el, month_el, day_el)

        # Fallback published from ArticleDate (electronic publication)
        if not result["published"][0]:
            article_date = root.find(".//ArticleDate")
            if article_date is not None:
                year_el = article_date.find("Year")
                month_el = article_date.find("Month")
                day_el = article_date.find("Day")
                result["published"] = _parse_pubmed_date_element(year_el, month_el, day_el)
    except Exception:
        logger.warning('处理失败，已跳过该项（_fetch_pubmed_dates）', exc_info=True)
        pass
    return result


def fetch_accepted_from_pubmed(pmid: str) -> DateResult:
    return _fetch_pubmed_dates(pmid)["accepted"]


def fetch_published_from_pubmed(pmid: str) -> DateResult:
    return _fetch_pubmed_dates(pmid)["published"]


def _resolve_pmid(doi: str) -> Optional[str]:
    if not doi:
        return None
    if not ESEARCH_URL:
        warn_missing("NCBI", "NCBI_BASE_URL")
        return None
    params = {
        "db": "pubmed",
        "retmode": "json",
        "retmax": 1,
        "term": f'{doi}[LID]',
        "api_key": PUBMED_API_KEY,
        "email": NCBI_EMAIL or None,
    }
    try:
        resp = requests.get(ESEARCH_URL, params=params, timeout=REQUEST_TIMEOUT, headers={"User-Agent": contact_user_agent()})
        resp.raise_for_status()
        data = resp.json()
        id_list = data.get("esearchresult", {}).get("idlist", [])
        return id_list[0] if id_list else None
    except Exception:
        logger.warning('处理失败，已跳过该项（_resolve_pmid）', exc_info=True)
        return None


def fetch_accepted_from_pubmed_by_doi(doi: str) -> DateResult:
    pmid = _resolve_pmid(doi)
    return fetch_accepted_from_pubmed(pmid) if pmid else (None, "")


def fetch_published_from_pubmed_by_doi(doi: str) -> DateResult:
    pmid = _resolve_pmid(doi)
    return fetch_published_from_pubmed(pmid) if pmid else (None, "")


def fetch_dates_from_pubmed(pmid: str | None) -> dict[str, DateResult]:
    """Fetch both accepted and published dates from PubMed by PMID."""
    if not pmid:
        return {"accepted": (None, ""), "published": (None, "")}
    return _fetch_pubmed_dates(pmid)


def fetch_dates_from_pubmed_by_doi(doi: str) -> dict[str, DateResult]:
    """Fetch both accepted and published dates from PubMed by DOI."""
    pmid = _resolve_pmid(doi)
    return fetch_dates_from_pubmed(pmid)


# ---------------------------------------------------------------------------
# Crossref extraction
# ---------------------------------------------------------------------------

def extract_accepted_from_crossref_meta(meta: dict) -> DateResult:
    if not meta or not isinstance(meta, dict):
        return None, ""
    msg = meta.get("message", meta)
    assertions = msg.get("assertion", [])
    if not assertions:
        return None, ""
    if isinstance(assertions, dict):
        assertions = [assertions]
    for a in assertions:
        name = str(a.get("name", "")).lower()
        if name in ("accepted", "date_accepted"):
            d, precision = _parse_date_str(a.get("value", ""))
            if d:
                return d, precision
    return None, ""


def extract_published_from_crossref_meta(meta: dict) -> DateResult:
    if not meta or not isinstance(meta, dict):
        return None, ""
    msg = meta.get("message", meta)

    def _from_date_parts(parts_list) -> DateResult:
        if not parts_list or not parts_list[0]:
            return None, ""
        dp = parts_list[0]
        try:
            year = int(dp[0])
            if len(dp) == 1:
                return datetime.date(year, 1, 1), "year"
            month = int(dp[1])
            if len(dp) == 2:
                return datetime.date(year, month, 1), "month"
            day = int(dp[2])
            return datetime.date(year, month, day), "day"
        except Exception:
            logger.debug("Crossref date parsing failed", exc_info=True)
            return None, ""

    # Try published-print first, then published-online
    for key in ("published-print", "published-online"):
        pub = msg.get(key)
        if pub and "date-parts" in pub:
            d, precision = _from_date_parts(pub["date-parts"])
            if d:
                return d, precision

    # Fallback to issued date
    issued = msg.get("issued")
    if issued and "date-parts" in issued:
        d, precision = _from_date_parts(issued["date-parts"])
        if d:
            return d, precision
    return None, ""


def _fetch_fresh_crossref(doi: str) -> Optional[dict]:
    if not doi:
        return None
    from core.crossref_api import fetch_metadata_by_doi
    try:
        result = fetch_metadata_by_doi(doi, structured=True)
        return result.get("raw", {})
    except Exception:
        logger.warning('处理失败，已跳过该项（_fetch_fresh_crossref）', exc_info=True)
        return None


def fetch_accepted_from_crossref(doi: str, existing_meta: dict | None = None) -> DateResult:
    if existing_meta:
        d = extract_accepted_from_crossref_meta(existing_meta)
        if d[0]:
            return d
    fresh = _fetch_fresh_crossref(doi)
    return extract_accepted_from_crossref_meta(fresh) if fresh else (None, "")


def fetch_published_from_crossref(doi: str, existing_meta: dict | None = None) -> DateResult:
    if existing_meta:
        d = extract_published_from_crossref_meta(existing_meta)
        if d[0]:
            return d
    fresh = _fetch_fresh_crossref(doi)
    return extract_published_from_crossref_meta(fresh) if fresh else (None, "")


# ---------------------------------------------------------------------------
# Cross-check helper
# ---------------------------------------------------------------------------

DATE_TOLERANCE_DAYS = 31


def enrich_paper_dates(paper) -> dict[str, tuple[Optional[datetime.date], Optional[str]]]:
    """Extract accepted and published dates for a Paper ORM object.
    
    Uses available fields: pmid, doi, doi_meta, extracted_text.
    Sets paper.accepted_date, paper.accepted_date_source, 
    paper.published_date, paper.published_date_source directly.
    
    Returns {"accepted": (date, source), "published": (date, source)}.
    """
    # --- PubMed ---
    pm_dates = {"accepted": (None, ""), "published": (None, "")}
    if paper.pmid:
        pm_dates = fetch_dates_from_pubmed(str(paper.pmid))
    elif paper.doi:
        pm_dates = fetch_dates_from_pubmed_by_doi(paper.doi)
    
    # --- Crossref ---
    crossref_meta = paper.doi_meta if (paper.doi_meta and isinstance(paper.doi_meta, dict)) else None
    if not crossref_meta and paper.doi:
        crossref_meta = _fetch_fresh_crossref(paper.doi)
    
    cr_accepted = extract_accepted_from_crossref_meta(crossref_meta) if crossref_meta else (None, "")
    cr_published = extract_published_from_crossref_meta(crossref_meta) if crossref_meta else (None, "")
    
    # --- PDF ---
    pdf_accepted = extract_accepted_from_pdf(paper.extracted_text) if paper.extracted_text else (None, "")
    pdf_published = extract_published_from_pdf(paper.extracted_text) if paper.extracted_text else (None, "")
    
    # --- Cross-check ---
    result = {}
    for field, pm, cr, pdf in [
        ("accepted", pm_dates["accepted"], cr_accepted, pdf_accepted),
        ("published", pm_dates["published"], cr_published, pdf_published),
    ]:
        best_date, source = cross_check(pm, cr, pdf)
        setattr(paper, f"{field}_date", best_date)
        setattr(paper, f"{field}_date_source", source)
        result[field] = (best_date, source)
    
    return result


def cross_check(
    pubmed: DateResult,
    crossref: DateResult,
    pdf: DateResult,
) -> tuple[Optional[datetime.date], Optional[str]]:
    """Cross-check three dates and return (best_date, source_tag).

    Source tags may include precision suffixes like crossref(year), pubmed(month).
    When multiple sources agree, the tag uses the best (most precise) precision.
    """
    date_pubmed, prec_pubmed = pubmed
    date_crossref, prec_crossref = crossref
    date_pdf, prec_pdf = pdf

    def agree(d1, d2):
        if d1 and d2:
            return abs((d1 - d2).days) <= DATE_TOLERANCE_DAYS
        return False

    def tag(source_name, precision):
        if precision == "day" or not precision:
            return source_name
        return f"{source_name}({precision})"

    def best_prec(*precs):
        order = {"year": 0, "month": 1, "day": 2, "": -1}
        best = max(precs, key=lambda p: order.get(p, -1))
        return best

    if agree(date_pubmed, date_crossref) and agree(date_pubmed, date_pdf):
        p = best_prec(prec_pubmed, prec_crossref, prec_pdf)
        return date_pubmed, tag("all", p)
    if agree(date_pubmed, date_pdf):
        p = best_prec(prec_pubmed, prec_pdf)
        return date_pubmed, tag("pubmed+pdf", p)
    if agree(date_crossref, date_pdf):
        p = best_prec(prec_crossref, prec_pdf)
        return date_crossref, tag("crossref+pdf", p)
    if agree(date_pubmed, date_crossref):
        p = best_prec(prec_pubmed, prec_crossref)
        return date_pubmed, tag("pubmed+crossref", p)
    if date_pubmed:
        return date_pubmed, tag("pubmed", prec_pubmed)
    if date_crossref:
        return date_crossref, tag("crossref", prec_crossref)
    if date_pdf:
        return date_pdf, tag("pdf", prec_pdf)
    return None, None
