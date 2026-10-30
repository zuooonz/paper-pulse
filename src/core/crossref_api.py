from __future__ import annotations

import re
import html
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing

DOI_REGEX = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")


def clean_crossref_abstract(value: Any) -> str:
    """Convert Crossref JATS/HTML abstracts to plain displayable text."""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    # Preserve structured-abstract headings before removing JATS/HTML tags.
    text = re.sub(
        r"(?is)<(?:jats:)?title\b[^>]*>\s*(.*?)\s*</(?:jats:)?title>",
        lambda match: f"\n{re.sub(r'<[^>]+>', ' ', match.group(1)).strip()}: ",
        text,
    )
    text = re.sub(r"(?is)</?(?:jats:)?(?:p|sec|break|br)\b[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    # Crossref payloads are occasionally encoded more than once
    # (for example ``&amp;lt;``).  Decode a small bounded number of times so
    # comparison operators become readable without risking an endless loop.
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    lines = [re.sub(r"[ \t]+", " ", line).strip(" :") for line in text.split("\n")]
    lines = [line for line in lines if line]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"(?i)^abstract\s*:?\s*\n?", "", cleaned).strip()
    return cleaned


def extract_doi_from_text(text: str) -> Optional[str]:
    """Scan the text to find a DOI string.

    DOI 通常出现在文章开头（标题页）或末尾（版权信息）。
    先搜索前 10000 字符，再搜索末尾 5000 字符，避免参考文献中间的误匹配。
    """
    if not text:
        return None

    # 1) 搜索文章开头
    match = DOI_REGEX.search(text[:10000])
    if match:
        return match.group(1).rstrip('.')

    # 2) 搜索文章末尾（版权/出版信息区域）
    tail = text[-5000:] if len(text) > 5000 else text
    match = DOI_REGEX.search(tail)
    if match:
        return match.group(1).rstrip('.')

    return None


def fetch_metadata_by_doi(doi: str, timeout_sec: int = 8, structured: bool = False) -> Dict[str, Any]:
    """
    Fetch metadata from Crossref API.
    When structured=False (default), returns backward compatible format:
        {'title', 'journal', 'authors_str', 'published_date', 'doi'}
    When structured=True, returns structured format:
        {
            "doi": str,
            "title": str,
            "journal": {"title": str, "issn": List[str], "eissn": Optional[str]},
            "year": Optional[int],
            "authors": [
                {"given": str, "family": str, "sequence": str,
                 "affiliations": List[str], "orcid": Optional[str]}
            ],
            "raw": dict  # original Crossref payload
        }
    Returns empty dict on 404 (DOI not found).
    Returns {"_error": "timeout/网络错误描述"} on network/timeout failures.
    """
    if not doi:
        return {}

    base_url = api_endpoint("CROSSREF_BASE_URL", "crossref_base_url")
    if not base_url:
        warn_missing("Crossref", "CROSSREF_BASE_URL")
        return {}
    headers = {"User-Agent": contact_user_agent()}
    url = f"{base_url}/works/{urllib.parse.quote(doi)}"
    mailto = configured_value("CROSSREF_MAILTO", "crossref", "mailto")

    try:
        if mailto:
            resp = requests.get(url, headers=headers, params={"mailto": mailto}, timeout=timeout_sec)
        else:
            resp = requests.get(url, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json().get("message", {})

        if not structured:
            # Original backward compatible format
            title = ""
            if data.get("title"):
                title = data["title"][0]

            journal = ""
            if data.get("container-title"):
                journal = data["container-title"][0]

            authors = []
            for a in data.get("author", []):
                given = a.get("given", "")
                family = a.get("family", "")
                seq = a.get("sequence", "")
                name = f"{given} {family}".strip()
                if not name:
                    continue

                # Simple heuristic annotations
                tags = []
                if seq == "first":
                    tags.append("第一作者")
                if "corresponding" in str(a).lower() or a.get("authenticated-orcid"):
                    # Crossref sometimes doesn't clearly mark corresponding author.
                    # This is a best effort annotation.
                    pass

                if tags:
                    name += f" ({', '.join(tags)})"
                authors.append(name)

            authors_str = "、 ".join(authors) if authors else ""

            # Extract published date
            date_str = ""
            pub = data.get("published-print", data.get("published-online", {}))
            if "date-parts" in pub and pub["date-parts"] and pub["date-parts"][0]:
                parts = pub["date-parts"][0]
                if len(parts) >= 2:
                    date_str = f"{parts[0]}-{parts[1]:02d}"
                elif len(parts) == 1:
                    date_str = str(parts[0])

            return {
                "title": title,
                "journal": journal,
                "authors_str": authors_str,
                "published_date": date_str,
                "doi": doi
            }

        else:
            # New structured format
            title = ""
            if data.get("title"):
                title = data["title"][0]

            # Extract journal info with ISSNs
            journal_title = ""
            if data.get("container-title"):
                journal_title = data["container-title"][0]
            issn_list: List[str] = data.get("ISSN", [])
            eissn: Optional[str] = None
            if "eISSN" in data:
                e_issn = data.get("eISSN", [])
                if e_issn:
                    eissn = e_issn[0] if isinstance(e_issn, list) else e_issn

            # Extract year
            year: Optional[int] = None
            pub = data.get("published-print", data.get("published-online", {}))
            if "date-parts" in pub and pub["date-parts"] and pub["date-parts"][0]:
                parts = pub["date-parts"][0]
                if parts:
                    try:
                        year = int(parts[0])
                    except (ValueError, TypeError):
                        pass

            # Extract structured authors
            structured_authors: List[Dict[str, Any]] = []
            for a in data.get("author", []):
                author = {
                    "given": a.get("given", ""),
                    "family": a.get("family", ""),
                    "sequence": a.get("sequence", ""),
                    "affiliations": [aff.get("name", "") for aff in a.get("affiliation", []) if aff.get("name")],
                    "orcid": a.get("ORCID", None) if a.get("ORCID") else None,
                }
                structured_authors.append(author)

            # Volume / issue / pages
            volume = data.get("volume", "")
            issue = data.get("issue", "")
            page = data.get("page", "")

            return {
                "doi": doi,
                "title": title,
                "journal": {
                    "title": journal_title,
                    "issn": issn_list,
                    "eissn": eissn,
                },
                "year": year,
                "volume": volume,
                "issue": issue,
                "page": page,
                "abstract": clean_crossref_abstract(data.get("abstract")),
                "authors": structured_authors,
                "raw": data,
            }

    except requests.Timeout:
        return {"_error": f"CrossRef 请求超时（>{timeout_sec}秒），请检查网络后重试"}
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {}
        return {"_error": f"CrossRef HTTP 错误: {e}"}
    except Exception as e:
        return {"_error": f"CrossRef 请求失败: {e}"}


def build_apa7_citation(meta: Dict[str, Any]) -> str:
    """Build APA 7th edition citation from CrossRef structured metadata.

    Format: Author, A. A., & Author, B. B. (Year). Title of article. *Journal Name*,
            *Volume*(Issue), Pages. https://doi.org/XXXX
    """
    if not meta or meta.get("_error"):
        return ""

    # Authors: up to 20 listed, 21+ uses "..." before last
    authors = meta.get("authors", [])
    author_str = ""
    if authors:
        names = []
        for a in authors:
            family = a.get("family", "")
            given = a.get("given", "")
            if family:
                initials = " ".join(g[:1].upper() + "." for g in given.split() if g) if given else ""
                names.append((family, initials))
        if len(names) > 20:
            names_list = [f"{f}, {i}" for f, i in names[:19]]
            names_list.append(f"...{names[-1][0]}, {names[-1][1]}")
            author_str = ", ".join(names_list)
        elif len(names) >= 2:
            author_str = ", ".join(f"{f}, {i}" for f, i in names[:-1])
            author_str += f", & {names[-1][0]}, {names[-1][1]}"
        elif len(names) == 1:
            author_str = f"{names[0][0]}, {names[0][1]}"
        author_str += " "

    # Year
    year = meta.get("year")
    year_str = f"({year}). " if year else ""

    # Title (sentence case)
    title = meta.get("title", "")
    if title:
        title = title[0].upper() + title[1:]  # capitalise first letter
        title += ". "

    # Journal (italic in output, marked with *)
    journal_title = ""
    if meta.get("journal") and isinstance(meta["journal"], dict):
        journal_title = meta["journal"].get("title", "")

    # Volume / issue / pages
    volume = meta.get("volume", "")
    issue = meta.get("issue", "")
    page = meta.get("page", "")

    vol_issue = ""
    if volume:
        vol_issue += f"*{volume}*"
        if issue:
            vol_issue += f"({issue})"
        vol_issue += ", " if page else ". "

    page_str = f"{page}. " if page else ""

    # DOI
    doi = meta.get("doi", "")
    doi_str = f"https://doi.org/{doi}" if doi else ""

    cite = f"{author_str}{year_str}{title}"
    if journal_title:
        cite += f"*{journal_title}*, {vol_issue}{page_str}"
    else:
        cite += f"{vol_issue}{page_str}"
    cite += doi_str

    return cite.strip()


def fetch_citation_by_doi(doi: str, timeout_sec: int = 10) -> str:
    """Fetch DOI metadata and build an APA 7th edition citation. Cached by DB paper fields."""
    meta = fetch_metadata_by_doi(doi, timeout_sec=timeout_sec, structured=True)
    return build_apa7_citation(meta)
