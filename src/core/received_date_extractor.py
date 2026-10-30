"""Extract paper received date from three sources:
1. PubMed API (via DOI -> PMID -> EFetch XML History)
2. Crossref metadata (assertion field in existing doi_meta, or fresh API call)
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
# PDF text extraction
# ---------------------------------------------------------------------------

# Common English month names
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

# Regex patterns for "Received ..." lines.
_RECEIVED_PATTERNS = [
    re.compile(
        r"Received[:\s]+(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Received[:\s]+(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Received[:\s]+([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Received[:\s]+([A-Za-z]+)\.?\s+(\d{4})",
        re.IGNORECASE,
    ),
]

_RECEIVED_LINE_RE = re.compile(
    r"^Received[:\s]+(.{5,60})$",
    re.IGNORECASE | re.MULTILINE,
)

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _parse_month(month_str: str) -> Optional[int]:
    """Convert month string to 1-12 integer."""
    if not month_str:
        return None
    m = month_str.strip().lower().rstrip(".")
    return _MONTH_MAP.get(m)


def _date_from_match(groups: tuple) -> Optional[datetime.date]:
    """Try to build a date from regex groups."""
    try:
        if len(groups) == 3:
            a, b, c = groups
            if str(a).isdigit() and len(str(a)) == 4:
                year, month, day = int(a), int(b), int(c)
                return datetime.date(year, month, day)
            if str(a).isdigit() and not str(b).isdigit():
                day = int(a)
                month = _parse_month(b)
                year = int(c)
                if month:
                    return datetime.date(year, month, day)
            if not str(a).isdigit() and str(b).isdigit():
                month = _parse_month(a)
                day = int(b)
                year = int(c)
                if month:
                    return datetime.date(year, month, day)
        elif len(groups) == 2:
            a, b = groups
            if not str(a).isdigit() and str(b).isdigit() and len(str(b)) == 4:
                month = _parse_month(a)
                year = int(b)
                if month:
                    return datetime.date(year, month, 1)
    except Exception:
        logger.debug('可选步骤失败，继续执行（_date_from_match）', exc_info=True)
        pass
    return None


def extract_from_pdf_text(text: str | None) -> Optional[datetime.date]:
    """Extract received date from PDF full-text. Returns first match or None."""
    if not text:
        return None

    for pattern in _RECEIVED_PATTERNS:
        match = pattern.search(text)
        if match:
            d = _date_from_match(match.groups())
            if d:
                return d

    for line_match in _RECEIVED_LINE_RE.finditer(text):
        line = line_match.group(1)
        if "revised" in line.lower():
            continue
        year_match = _YEAR_RE.search(line)
        if year_match:
            year = int(year_match.group(1))
            dm_match = re.search(
                r"(\d{1,2})\s+([A-Za-z]+)\b", line, re.IGNORECASE
            ) or re.search(
                r"([A-Za-z]+)\b\.?\s+(\d{1,2})", line, re.IGNORECASE
            )
            if dm_match:
                a, b = dm_match.groups()
                if a.isdigit():
                    day, month_str = int(a), b
                else:
                    month_str, day = a, int(b)
                month = _parse_month(month_str)
                if month:
                    try:
                        return datetime.date(year, month, day)
                    except Exception:
                        logger.warning('处理失败，已跳过该项（extract_from_pdf_text）', exc_info=True)
                        pass
            try:
                return datetime.date(year, 1, 1)
            except Exception:
                logger.warning('处理失败，已跳过该项（extract_from_pdf_text）', exc_info=True)
                pass

    return None


# ---------------------------------------------------------------------------
# PubMed API extraction
# ---------------------------------------------------------------------------

NCBI_BASE_URL = api_endpoint("NCBI_BASE_URL", "ncbi_base_url")
ESEARCH_URL = f"{NCBI_BASE_URL}/esearch.fcgi" if NCBI_BASE_URL else ""
EFETCH_URL = f"{NCBI_BASE_URL}/efetch.fcgi" if NCBI_BASE_URL else ""
PUBMED_API_KEY = configured_value("NCBI_API_KEY", "ncbi", "api_key") or None
NCBI_EMAIL = configured_value("NCBI_EMAIL", "ncbi", "email")
REQUEST_TIMEOUT = 15


def _parse_pubmed_date_element(year_el, month_el, day_el) -> Optional[datetime.date]:
    """Parse PubMed XML date elements."""
    try:
        year = int(year_el.text) if year_el is not None and year_el.text else None
        if not year:
            return None
        month_text = month_el.text.strip() if month_el is not None and month_el.text else "1"
        day_text = day_el.text.strip() if day_el is not None and day_el.text else "1"

        if month_text.isdigit():
            month = int(month_text)
        else:
            month = _parse_month(month_text)
        if not month:
            month = 1

        day = int(day_text) if day_text.isdigit() else 1
        return datetime.date(year, month, day)
    except Exception:
        logger.debug('可选步骤失败，继续执行（_parse_pubmed_date_element）', exc_info=True)
        return None


def fetch_from_pubmed_by_pmid(pmid: str) -> Optional[datetime.date]:
    """Fetch received date from PubMed EFetch XML by PMID."""
    if not pmid:
        return None
    if not EFETCH_URL:
        warn_missing("NCBI", "NCBI_BASE_URL")
        return None
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
        for hist in root.findall(".//PubMedPubDate"):
            status = hist.get("PubStatus", "").lower()
            if status == "received":
                year_el = hist.find("Year")
                month_el = hist.find("Month")
                day_el = hist.find("Day")
                d = _parse_pubmed_date_element(year_el, month_el, day_el)
                if d:
                    return d
        return None
    except Exception:
        logger.warning('处理失败，已跳过该项（fetch_from_pubmed_by_pmid）', exc_info=True)
        return None


def fetch_from_pubmed_by_doi(doi: str) -> Optional[datetime.date]:
    """Resolve DOI -> PMID via ESearch, then fetch received date."""
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
        if id_list:
            return fetch_from_pubmed_by_pmid(id_list[0])
    except Exception:
        logger.warning('处理失败，已跳过该项（fetch_from_pubmed_by_doi）', exc_info=True)
        pass
    return None


# ---------------------------------------------------------------------------
# Crossref extraction (from doi_meta or API)
# ---------------------------------------------------------------------------

# Crossref assertion date formats
def _parse_crossref_date_str(val: str) -> Optional[datetime.date]:
    """Parse a date string from Crossref assertion.
    
    Supported formats:
      - YYYY-MM-DD
      - DD Month YYYY
      - Month DD, YYYY
      - Month YYYY
    """
    if not val:
        return None
    val = str(val).strip()

    # ISO format: YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", val)
    if m:
        try:
            return datetime.date(int(m[1]), int(m[2]), int(m[3]))
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_crossref_date_str）', exc_info=True)
            pass

    # Day Month Year: 20 July 2018
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", val, re.IGNORECASE)
    if m:
        try:
            day = int(m[1])
            month = _parse_month(m[2])
            year = int(m[3])
            if month:
                return datetime.date(year, month, day)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_crossref_date_str）', exc_info=True)
            pass

    # Month Day, Year: July 20, 2018
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", val, re.IGNORECASE)
    if m:
        try:
            month = _parse_month(m[1])
            day = int(m[2])
            year = int(m[3])
            if month:
                return datetime.date(year, month, day)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_crossref_date_str）', exc_info=True)
            pass

    # Month Year: July 2018
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", val, re.IGNORECASE)
    if m:
        try:
            month = _parse_month(m[1])
            year = int(m[2])
            if month:
                return datetime.date(year, month, 1)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_parse_crossref_date_str）', exc_info=True)
            pass

    return None


def extract_from_crossref_meta(meta: dict) -> Optional[datetime.date]:
    """Extract received date from existing Crossref doi_meta dict."""
    if not meta or not isinstance(meta, dict):
        return None
    msg = meta.get("message", meta)
    assertions = msg.get("assertion", [])
    if not assertions:
        return None
    if isinstance(assertions, dict):
        assertions = [assertions]
    for a in assertions:
        name = str(a.get("name", "")).lower()
        if name in ("received", "date_received"):
            val = a.get("value")
            if val:
                d = _parse_crossref_date_str(val)
                if d:
                    return d
    return None


def fetch_from_crossref_by_doi(doi: str) -> Optional[datetime.date]:
    """Fetch fresh Crossref metadata and extract received date."""
    if not doi:
        return None
    from core.crossref_api import fetch_metadata_by_doi
    try:
        result = fetch_metadata_by_doi(doi, structured=True)
        raw = result.get("raw", {})
        return extract_from_crossref_meta(raw)
    except Exception:
        logger.warning('处理失败，已跳过该项（fetch_from_crossref_by_doi）', exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Cross-check helper
# ---------------------------------------------------------------------------

RECEIVED_DATE_TOLERANCE_DAYS = 31


def cross_check(
    date_pubmed: Optional[datetime.date],
    date_crossref: Optional[datetime.date],
    date_pdf: Optional[datetime.date],
) -> tuple[Optional[datetime.date], Optional[str]]:
    """Cross-check three dates and return (best_date, source_tag).

    Source tags:
        "all"            – all three sources agree
        "pubmed+pdf"     – PubMed and PDF agree
        "crossref+pdf"   – Crossref and PDF agree
        "pubmed+crossref"– PubMed and Crossref agree
        "pubmed"         – only PubMed succeeded
        "crossref"       – only Crossref succeeded
        "pdf"            – only PDF succeeded
        None             – all failed
    """
    # Helper: check if two dates agree
    def agree(d1, d2):
        if d1 and d2:
            return abs((d1 - d2).days) <= RECEIVED_DATE_TOLERANCE_DAYS
        return False

    # Three-way agreement
    if agree(date_pubmed, date_crossref) and agree(date_pubmed, date_pdf):
        return date_pubmed, "all"

    # Two-way agreements
    if agree(date_pubmed, date_pdf):
        return date_pubmed, "pubmed+pdf"
    if agree(date_crossref, date_pdf):
        return date_crossref, "crossref+pdf"
    if agree(date_pubmed, date_crossref):
        return date_pubmed, "pubmed+crossref"

    # Single source fallback (prefer PubMed > Crossref > PDF)
    if date_pubmed:
        return date_pubmed, "pubmed"
    if date_crossref:
        return date_crossref, "crossref"
    if date_pdf:
        return date_pdf, "pdf"

    return None, None
