"""Affiliation normalization engine.

Pipeline:
  1. Clean raw string (lowercase, strip punctuation, unicode normalize)
  2. Extract signature (key tokens: university/hospital/city)
  3. Fuzzy match to existing canonical entries
  4. Create new canonical if no match

Designed for Chinese medical and academic institution names in research papers.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional


# ══════════════════════════════════════════════════════════════════════
# Cleaning
# ══════════════════════════════════════════════════════════════════════

# Common noise patterns in CrossRef affiliations
_NOISE_RES = [
    # HTML entities
    (re.compile(r"&#x00[0-9a-f]{2};"), ""),
    (re.compile(r"&#x201[0-9a-f];"), "'"),
    (re.compile(r"&amp;"), "&"),
    # Unicode escape-like artifacts
    (re.compile(r"x002[0-9a-f]"), ""),
    (re.compile(r"x201[0-9a-f]"), "'"),
    # Clipboard corruption
    (re.compile(r"\u00ad"), ""),  # soft hyphen
    (re.compile(r"\u200b"), ""),  # zero-width space
    # Broken hyphens
    (re.compile(r"\u2010|\u2011|\u2012|\u2013|\u2014"), "-"),
    # Multiple spaces
    (re.compile(r"\s+"), " "),
]

# Patterns to strip from affiliation for cleaner matching
_STRIP_PATTERNS = [
    # Postal codes
    re.compile(r"\b\d{5,6}(-\d+)?\b"),
    # Country suffixes
    re.compile(r",?\s*(people'?s republic of )?(pr )?china\.?$", re.IGNORECASE),
    # City suffixes
    re.compile(r",?\s*(beijing|shanghai|guangzhou|nanjing|tianjin|shenzhen|hangzhou|wuhan|chengdu|chongqing|suzhou|changsha|jinan|zhengzhou)\s*\d{0,6},?\s*china\.?$", re.IGNORECASE),
    # Province suffixes
    re.compile(r",?\s*(guangdong|jiangsu|zhejiang|shandong|sichuan|hubei|hunan|henan|fujian|hebei|anhui|shaanxi|liaoning|jilin|heilongjiang|hainan|yunnan|guizhou|gansu)\s*\d{0,6},?\s*china\.?$", re.IGNORECASE),
    # Ordinal suffixes in hospital names
    re.compile(r"\b(\d+)(?:st|nd|rd|th)\b"),
    # Leading/trailing junk
    re.compile(r"^[,;\s]+|[,;\s]+$"),
]


def clean_affiliation(raw: str) -> str:
    """Normalize an affiliation string for comparison.

    Returns a cleaned, lowercased string with noise removed.
    Does NOT strip semantically meaningful content.
    """
    if not raw:
        return ""
    s = raw.strip()
    for pat, repl in _NOISE_RES:
        s = pat.sub(repl, s)
    s = s.lower().strip()
    # Remove common noise but keep structure
    for pat in _STRIP_PATTERNS:
        s = pat.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[,;]+$", "", s).strip()
    return s


# ══════════════════════════════════════════════════════════════════════
# Token extraction (signature for matching)
# ══════════════════════════════════════════════════════════════════════

# University name patterns (Chinese + international)
_UNIV_WORDS = {
    "university", "univ", "college", "school", "institute",
    "universität", "universite", "universidad",
}

# Hospital/department indicators
_HOSPITAL_WORDS = {
    "hospital", "medical", "clinic", "center", "centro",
    "rehabilitation", "rehab", "surgery", "medicine",
    "neurology", "pediatric", "children", "ophthalmology",
}

# Known Chinese university canonical short names
_UNIV_SHORT_MAP = {
    "beihang": "beihang university",
    "fudan": "fudan university",
    "peking": "peking university",
    "tsinghua": "tsinghua university",
    "zhejiang": "zhejiang university",
    "shanghai jiao tong": "shanghai jiao tong university",
    "sun yat-sen": "sun yat-sen university",
    "sun yatsen": "sun yat-sen university",
    "nanjing medical": "nanjing medical university",
    "capital medical": "capital medical university",
    "tianjin medical": "tianjin medical university",
    "southern medical": "southern medical university",
    "shandong": "shandong university",
    "wuhan": "wuhan university",
    "huazhong": "huazhong university of science and technology",
    "tongji": "tongji university",
    "sichuan": "sichuan university",
    "xi'an jiaotong": "xi'an jiaotong university",
    "central south": "central south university",
    "harbin medical": "harbin medical university",
    "china medical": "china medical university",
    "chongqing medical": "chongqing medical university",
    "anhui medical": "anhui medical university",
    "guangzhou medical": "guangzhou medical university",
    "wenzhou medical": "wenzhou medical university",
    "xuzhou medical": "xuzhou medical university",
    "fujian medical": "fujian medical university",
    "guangxi medical": "guangxi medical university",
}


def extract_signature(cleaned: str) -> dict:
    """Extract semantic tokens from a cleaned affiliation string.

    Returns a dict with:
      - university: detected university canonical name (or None)
      - hospital: detected hospital name (or None)
      - department: detected department(s)
      - city: detected city name (or None)
      - tokens: key tokens for fuzzy matching
    """
    sig = {
        "university": None,
        "hospital": None,
        "department": None,
        "city": None,
        "tokens": [],
    }

    if not cleaned:
        return sig

    words = cleaned.replace(",", " ").split()
    sig["tokens"] = [w for w in words if len(w) > 2 or w in ("ai", "ar", "vr", "uk", "us")]

    # Detect university
    cleaned_lower = cleaned.lower()
    for short, canonical in sorted(_UNIV_SHORT_MAP.items(), key=lambda x: -len(x[0])):
        if short in cleaned_lower:
            sig["university"] = canonical
            break
    if not sig["university"]:
        # Fallback: look for "XXX university" pattern
        m = re.search(r"([a-z\s]+(?:university|univ\.?))", cleaned_lower)
        if m:
            sig["university"] = m.group(1).strip()

    # Detect hospital (first hospital name mentioned)
    m = re.search(r"(?:the\s+)?(?:affiliated\s+)?([a-z\s]{3,60}?)\s*hospital", cleaned_lower)
    if m:
        sig["hospital"] = m.group(1).strip() + " hospital"

    # Detect department
    dept_patterns = [
        r"department of ([a-z\s]{3,60}?)(?:,|$|\.|\d)",
        r"school of ([a-z\s]{3,40}?)(?:,|$|\.|\d)",
        r"college of ([a-z\s]{3,40}?)(?:,|$|\.|\d)",
        r"institute of ([a-z\s]{3,40}?)(?:,|$|\.|\d)",
        r"(?:key )?laboratory (?:of|for) ([a-z\s]{3,60}?)(?:,|$|\.|\d)",
    ]
    for pat in dept_patterns:
        m = re.search(pat, cleaned_lower)
        if m:
            sig["department"] = m.group(1).strip()
            break

    # Detect city (common Chinese cities)
    _CITIES = [
        "beijing", "shanghai", "guangzhou", "nanjing", "tianjin",
        "shenzhen", "hangzhou", "wuhan", "chengdu", "chongqing",
        "suzhou", "changsha", "jinan", "zhengzhou", "xian",
        "wenzhou", "fuzhou", "xiamen", "harbin", "shenyang",
        "hefei", "nanchang", "kunming", "guiyang", "lanzhou",
        "nanning", "haikou", "urumqi", "lasa", "xining",
        "yinchuan", "taiyuan", "shijiazhuang", "changchun",
        "london", "boston", "new york", "chicago", "pittsburgh",
        "baltimore", "los angeles", "san francisco", "seattle",
        "cambridge", "oxford", "berlin", "munich", "paris",
        "tokyo", "seoul", "singapore", "toronto", "montreal",
        "vancouver", "sydney", "melbourne", "cork",
        "stanmore", "oklahoma city",
    ]
    for city in sorted(_CITIES, key=len, reverse=True):
        if city in cleaned_lower:
            sig["city"] = city
            break

    return sig


# ══════════════════════════════════════════════════════════════════════
# Similarity
# ══════════════════════════════════════════════════════════════════════

def string_similarity(a: str, b: str) -> float:
    """Compute similarity between two cleaned affiliation strings."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def signature_similarity(sig_a: dict, sig_b: dict) -> float:
    """Compute similarity between two affiliation signatures.

    Weighted: university match > hospital match > department match.
    """
    score = 0.0
    total_weight = 0.0

    # University: highest weight
    if sig_a.get("university") and sig_b.get("university"):
        total_weight += 4.0
        if sig_a["university"] == sig_b["university"]:
            score += 4.0
        else:
            # Partial match only if one contains the other
            if sig_a["university"] in sig_b["university"] or sig_b["university"] in sig_a["university"]:
                score += 2.5
            else:
                # Mismatched universities → penalize
                score -= 3.0
    elif sig_a.get("university") or sig_b.get("university"):
        # One has, one doesn't → require hospital match to compensate
        if not (sig_a.get("hospital") and sig_b.get("hospital")):
            return 0.0  # No university match + no hospital match → reject
        total_weight += 1.0

    # Hospital
    if sig_a.get("hospital") and sig_b.get("hospital"):
        total_weight += 3.0
        if sig_a["hospital"] == sig_b["hospital"]:
            score += 3.0
        elif sig_a["hospital"] in sig_b["hospital"] or sig_b["hospital"] in sig_a["hospital"]:
            score += 2.0
        else:
            score -= 2.0  # Different hospitals → penalize
    elif sig_a.get("hospital") or sig_b.get("hospital"):
        total_weight += 1.0

    # Department
    if sig_a.get("department") and sig_b.get("department"):
        total_weight += 1.0
        if sig_a["department"] == sig_b["department"]:
            score += 1.0

    # City
    if sig_a.get("city") and sig_b.get("city"):
        total_weight += 1.0
        if sig_a["city"] == sig_b["city"]:
            score += 1.0

    # Token overlap (Jaccard)
    tokens_a = set(sig_a.get("tokens", []))
    tokens_b = set(sig_b.get("tokens", []))
    if tokens_a and tokens_b:
        jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
        total_weight += 1.0
        score += jaccard

    if total_weight == 0:
        return 0.0
    return score / total_weight


# ══════════════════════════════════════════════════════════════════════
# Canonical name generation
# ══════════════════════════════════════════════════════════════════════

def generate_canonical_name(cleaned: str, sig: dict) -> str:
    """Generate a canonical English name from a cleaned affiliation string.

    Format: "Department, Hospital, University, City"
    """
    parts = []

    # University
    if sig.get("university"):
        parts.append(sig["university"].title())

    # Hospital
    if sig.get("hospital"):
        h = sig["hospital"]
        # Auto-prefix "Affiliated" for Chinese hospital patterns
        if (sig.get("university") and
            not h.lower().startswith(("affiliated", "the ")) and
            "hospital" in h.lower()):
            h = h.strip()
            # Check if it's a named hospital
            if not any(w in h.lower() for w in ["huashan", "zhongshan", "tongren",
                                                  "xuanwu", "tiantan", "ruijin",
                                                  "zhujiang", "nanfang", "changhai"]):
                pass  # use as-is
        parts.append(h.title())

    # Department
    if sig.get("department"):
        d = sig["department"]
        parts.append(f"Department of {d.title()}")

    # City
    if sig.get("city") and sig["city"] not in " ".join(parts).lower():
        parts.append(sig["city"].title())

    if not parts:
        return cleaned[:200].strip()

    result = ", ".join(parts)
    return result[:500]


# ══════════════════════════════════════════════════════════════════════
# Country inference
# ══════════════════════════════════════════════════════════════════════

# City → country map
_CITY_COUNTRY = {
    "beijing": "China", "shanghai": "China", "guangzhou": "China",
    "nanjing": "China", "tianjin": "China", "shenzhen": "China",
    "hangzhou": "China", "wuhan": "China", "chengdu": "China",
    "chongqing": "China", "suzhou": "China", "changsha": "China",
    "jinan": "China", "zhengzhou": "China", "xian": "China",
    "wenzhou": "China", "fuzhou": "China", "xiamen": "China",
    "harbin": "China", "shenyang": "China", "hefei": "China",
    "nanchang": "China", "kunming": "China", "guiyang": "China",
    "lanzhou": "China", "nanning": "China", "haikou": "China",
    "urumqi": "China", "lasa": "China", "xining": "China",
    "yinchuan": "China", "taiyuan": "China", "shijiazhuang": "China",
    "changchun": "China",
    "london": "United Kingdom", "boston": "United States",
    "new york": "United States", "chicago": "United States",
    "pittsburgh": "United States", "baltimore": "United States",
    "los angeles": "United States", "san francisco": "United States",
    "seattle": "United States", "cambridge": "United States",
    "oxford": "United Kingdom", "berlin": "Germany",
    "munich": "Germany", "paris": "France",
    "tokyo": "Japan", "seoul": "South Korea",
    "singapore": "Singapore", "toronto": "Canada",
    "montreal": "Canada", "vancouver": "Canada",
    "sydney": "Australia", "melbourne": "Australia",
    "cork": "Ireland", "stanmore": "United Kingdom",
    "oklahoma city": "United States",
}

# Province → country
_PROVINCE_COUNTRY = {
    "guangdong": "China", "jiangsu": "China", "zhejiang": "China",
    "shandong": "China", "sichuan": "China", "hubei": "China",
    "hunan": "China", "henan": "China", "fujian": "China",
    "hebei": "China", "anhui": "China", "shaanxi": "China",
    "liaoning": "China", "jilin": "China", "heilongjiang": "China",
    "hainan": "China", "yunnan": "China", "guizhou": "China",
    "gansu": "China", "jiangxi": "China", "shanxi": "China",
    "qinghai": "China", "guangxi": "China", "nei mongol": "China",
    "xinjiang": "China", "tibet": "China", "ningxia": "China",
}

# Country keyword patterns
_COUNTRY_PATTERNS = [
    (re.compile(r"\b(peking|beijing|shanghai|guangzhou|nanjing|tianjin|shenzhen|hangzhou|wuhan|chengdu|chongqing|suzhou|changsha)\b", re.I), "China"),
    (re.compile(r"\b(guangdong|jiangsu|zhejiang|shandong|sichuan|hubei|hunan|henan|fujian)\b", re.I), "China"),
    (re.compile(r"\bchina\b", re.I), "China"),
    (re.compile(r"\bunited states\b|\busa\b|\bu\.?s\.?a\.?\b|\bamerican\b|\bunited state\b", re.I), "United States"),
    (re.compile(r"\bunited kingdom\b|\buk\b|\bbritain\b|\bbritish\b|\bengland\b", re.I), "United Kingdom"),
    (re.compile(r"\bgermany\b|\bgerman\b|\bdeutschland\b", re.I), "Germany"),
    (re.compile(r"\bjapan\b|\bjapanese\b", re.I), "Japan"),
    (re.compile(r"\bkorea\b|\bkorean\b", re.I), "South Korea"),
    (re.compile(r"\bturkey\b|\bturkish\b", re.I), "Turkey"),
    (re.compile(r"\bgreece\b|\bgreek\b", re.I), "Greece"),
    (re.compile(r"\bromania\b|\bromanian\b", re.I), "Romania"),
    (re.compile(r"\bcanada\b|\bcanadian\b", re.I), "Canada"),
    (re.compile(r"\baustralia\b|\baustralian\b", re.I), "Australia"),
    (re.compile(r"\bfrance\b|\bfrench\b", re.I), "France"),
    (re.compile(r"\bitaly\b|\bitalian\b", re.I), "Italy"),
    (re.compile(r"\bspain\b|\bspanish\b", re.I), "Spain"),
    (re.compile(r"\bnetherlands\b|\bdutch\b", re.I), "Netherlands"),
    (re.compile(r"\bswitzerland\b|\bswiss\b", re.I), "Switzerland"),
    (re.compile(r"\bsingapore\b", re.I), "Singapore"),
    (re.compile(r"\bsingapore\b", re.I), "Singapore"),
    (re.compile(r"\bsweden\b|\bswedish\b", re.I), "Sweden"),
    (re.compile(r"\bireland\b|\birish\b", re.I), "Ireland"),
    (re.compile(r"\bbelgium\b|\bbelgian\b", re.I), "Belgium"),
    (re.compile(r"\bdenmark\b|\bdanish\b", re.I), "Denmark"),
    (re.compile(r"\bfinland\b|\bfinnish\b", re.I), "Finland"),
    (re.compile(r"\bnorway\b|\bnorwegian\b", re.I), "Norway"),
    (re.compile(r"\baustria\b|\baustrian\b", re.I), "Austria"),
    (re.compile(r"\bbrazil\b|\bbrazili(?:a)n\b", re.I), "Brazil"),
    (re.compile(r"\bindia\b|\bindian\b", re.I), "India"),
    (re.compile(r"\brussia\b|\brussian\b", re.I), "Russia"),
    (re.compile(r"\btaiwan\b|\btaiwanese\b", re.I), "Taiwan"),
    (re.compile(r"\bhong kong\b", re.I), "Hong Kong"),
    (re.compile(r"\bnew zealand\b", re.I), "New Zealand"),
    # Institution names that signal country (no explicit country mention)
    (re.compile(r"\b(johns hopkins|carnegie mellon|northwestern university|penn state|pennsylvania state|baylor university|wheaton college|university of (michigan|tennessee|pittsburgh|chicago|florida|texas|california|illinois|ohio|washington|arizona|colorado|georgia|hawaii|kansas|kentucky|louisiana|maryland|massachusetts|minnesota|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|north carolina|north dakota|oklahoma|oregon|rhode island|south carolina|south dakota|utah|vermont|virginia|west virginia|wisconsin|wyoming|alabama|alaska|arkansas|connecticut|delaware|idaho|indiana|iowa|maine|mississippi))\b", re.I), "United States"),
    # Chinese universities without explicit country
    (re.compile(r"\b(northwest a&f|soochow university|jiangnan university|jiangsu university|university of macau|shenzhen university|south china|guangdong university)\b", re.I), "China"),
    (re.compile(r"\bmacau\b|\bmacao\b", re.I), "Macau"),
    # US state abbreviations
    (re.compile(r"\b(tx|ca|fl|ny|il|pa|ma|oh|ga|az|co|ct|de|hi|ia|id|in|ks|ky|la|md|me|mi|mn|mo|ms|mt|nc|nd|ne|nh|nj|nm|nv|ok|or|ri|sc|sd|tn|ut|va|vt|wa|wi|wv|wy)\b", re.I), "United States"),
    # US city/state patterns
    (re.compile(r"\b(baltimore|boston|chicago|houston|dallas|philadelphia|phoenix|san diego|san antonio|detroit|indianapolis|jacksonville|columbus|charlotte|seattle|denver|washington dc|nashville|portland|oklahoma city|memphis|louisville|milwaukee|albuquerque|tucson|fresno|sacramento|mesa|atlanta|omaha|colorado springs|long beach|virginia beach|raleigh|miami|oakland|minneapolis|tulsa|wichita|new orleans|arlington|cleveland|bakersfield|aurora|anaheim|honolulu|lexington|stockton|corpus christi|henderson|riverside|newark|orlando|chula vista|laredo|madison|lubbock|chandler|buffalo|lincoln|fort wayne|jersey city|chattanooga|chesapeake|bellevue|torrance|rockford|pomona|paterson|pasadena|bridgeport|hayward|moreno valley|broken arrow|lakewood)\b", re.I), "United States"),
    (re.compile(r"\bmacau\b|\bmacao\b", re.I), "Macau"),
    (re.compile(r"\bmichigan\b|\btexas\b|\bcalifornia\b|\bflorida\b|\bohio\b|\billinois\b|\bpennsylvania\b|\bmassachusetts\b|\btennessee\b|\bcarolina\b|\bvirginia\b|\bwisconsin\b|\bminnesota\b|\bcolorado\b|\barizona\b|\bgeorgia\b|\bwashington\b|\bmaryland\b", re.I), "United States"),
    (re.compile(r"\brhode island\b|\bconnecticut\b|\bmaine\b|\bvermont\b|\bnebraska\b|\bkansas\b|\bmontana\b|\bnevada\b|\bhawaii\b|\borleans\b", re.I), "United States"),
    (re.compile(r"\btx\b|\bca\b|\bfl\b|\bny\b|\bil\b|\bpa\b|\bma\b|\boh\b", re.I), "United States"),
]


def infer_country(text: str) -> Optional[str]:
    """Infer country from affiliation text or city name."""
    if not text:
        return None
    text_lower = text.lower()

    # Check city first
    for city, country in sorted(_CITY_COUNTRY.items(), key=lambda x: -len(x[0])):
        if city in text_lower:
            return country

    # Check province
    for province, country in _PROVINCE_COUNTRY.items():
        if province in text_lower:
            return country

    # Check patterns
    for pat, country in _COUNTRY_PATTERNS:
        if pat.search(text_lower):
            return country

    return None


# ══════════════════════════════════════════════════════════════════════
# Chinese name matching helpers
# ══════════════════════════════════════════════════════════════════════

def pinyin_syllables(cn_name: str) -> list[str]:
    """Get pinyin syllables for a Chinese name."""
    try:
        from pypinyin import pinyin, Style
        syllables = [s[0] for s in pinyin(cn_name, style=Style.NORMAL)]
        # The library selects the most common pronunciation without surname
        # context. These are standard polyphonic Chinese surname readings.
        surname_overrides = {
            "单": "shan", "翟": "zhai", "曾": "zeng", "仇": "qiu",
            "解": "xie", "查": "zha", "朴": "piao", "区": "ou", "卜": "bu",
            "乐": "yue", "缪": "miao", "繁": "po", "员": "yun",
            "阚": "kan",
        }
        if cn_name and cn_name[0] in surname_overrides and syllables:
            syllables[0] = surname_overrides[cn_name[0]]
        return syllables
    except ImportError:
        return []


def match_cn_to_en(cn_name: str, en_name: str) -> bool:
    """Check if a Chinese name matches an English name via pinyin."""
    if not cn_name or not en_name:
        return False

    syllables = pinyin_syllables(cn_name)
    if not syllables:
        return False

    # Publisher metadata commonly uses non-ASCII hyphens (U+2010, U+2011,
    # U+2013, etc.).  Splitting only on ``-`` caused otherwise exact matches
    # such as 王晓明 ↔ Xiao‐Ming Wang to fail and left duplicate relations.
    normalized_en = unicodedata.normalize("NFKD", en_name).casefold()
    normalized_en = "".join(
        char for char in normalized_en if not unicodedata.combining(char)
    )
    en_parts = re.findall(r"[a-z]+", normalized_en)
    syllables = [part.replace("v", "u") for part in syllables]

    n = len(syllables)
    # Build all consecutive concatenations. Keep every range because repeated
    # syllables are common in Chinese given names (for example 王旺 -> Wang Wang).
    combo_ranges: dict[str, list[tuple[int, int]]] = {}
    for i in range(n):
        s = ""
        for j in range(i, n):
            s += syllables[j]
            combo_ranges.setdefault(s, []).append((i, j + 1))

    def assign(part_index: int, used: tuple[bool, ...]) -> bool:
        if part_index == len(en_parts):
            return all(used)
        for start, end in combo_ranges.get(en_parts[part_index], []):
            if any(used[start:end]):
                continue
            updated = list(used)
            for index in range(start, end):
                updated[index] = True
            if assign(part_index + 1, tuple(updated)):
                return True
        return False

    return assign(0, tuple(False for _ in range(n)))


def match_cn_to_en_fuzzy(cn_name: str, en_name: str) -> bool:
    """Allow limited publisher variants while preserving surname/name structure.

    Besides one-character romanization differences, publication names may
    contain an initial or an adopted given name (for example ``Chuanxin M.
    Niu`` or ``Christina Zong-Hao Ma``).  We only accept a unique candidate at
    the caller, so the Chinese pinyin still has to cover the surname and one
    contiguous run of non-initial given-name tokens.
    """
    if match_cn_to_en(cn_name, en_name):
        return True
    syllables = [part.replace("v", "u") for part in pinyin_syllables(cn_name)]
    normalized_en = unicodedata.normalize("NFKD", en_name).casefold()
    normalized_en = "".join(
        char for char in normalized_en if not unicodedata.combining(char)
    )
    parts = re.findall(r"[a-z]+", normalized_en)
    if len(syllables) < 2 or len(parts) < 2:
        return False
    surname = syllables[0]
    given = "".join(syllables[1:])
    for surname_index, part in enumerate(parts):
        if SequenceMatcher(None, surname, part).ratio() < 0.8:
            continue
        remaining = [
            value for index, value in enumerate(parts)
            if index != surname_index and len(value) > 1
        ]
        for start in range(len(remaining)):
            candidate = ""
            for end in range(start, len(remaining)):
                candidate += remaining[end]
                if SequenceMatcher(None, given, candidate).ratio() >= 0.83:
                    return True
    return False
