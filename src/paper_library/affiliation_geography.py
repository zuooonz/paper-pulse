"""Evidence-based affiliation parsing and map-provider reconciliation."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import requests

from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing


_INSTITUTION_ENDINGS = re.compile(
    r"(?:附属[^，,；;、]{0,20}?医院|医院|大学|研究院|研究所|学院|医学中心|医疗中心|"
    r"康复中心|卫生中心|疾控中心|疾病预防控制中心|诊所|卫生院)"
)
_DEPARTMENT_PREFIX = re.compile(
    r"^(?:附属)?(?:[一二三四五六七八九十]+)?(?:临床)?(?:康复|神经|精神|心理|儿|脑|运动|"
    r"骨|心|老年|麻醉|影像|生物|认知|发育|物理|中医|针灸|教育|体育|工程|医学)"
)


def normalize_place(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip().casefold()
    text = re.sub(r"(?:壮族|回族|维吾尔|蒙古族|藏族)?自治区$", "", text)
    return re.sub(r"(?:特别行政区|省|市|地区|盟|自治州|区|县)$", "", text)


def normalize_institution(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def split_institution_department(affiliation: str) -> tuple[str, str]:
    """Split at the last institution ending; never treat a department as a POI."""
    cleaned = re.sub(r"\s+", "", affiliation or "").strip("，,；;、 ")
    cleaned = re.sub(r"\b\d{5,6}\b.*$", "", cleaned)
    cleaned = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}.*$", "", cleaned, flags=re.I)
    if not cleaned:
        return "", ""
    first = re.split(r"[；;\n]+", cleaned, maxsplit=1)[0]
    matches = list(_INSTITUTION_ENDINGS.finditer(first))
    if not matches:
        return first, ""
    end = matches[-1].end()
    institution, remainder = first[:end].strip("，,、 "), first[end:].strip("，,、 ")
    if remainder and not _DEPARTMENT_PREFIX.search(remainder):
        # Address fragments are useful to the provider but are not departments.
        if re.search(r"(?:路|街|号|区|县)", remainder):
            return first, ""
    return institution, remainder


@dataclass(frozen=True)
class GeoCandidate:
    provider: str
    query_text: str
    institution_query: str
    department_query: str
    result_name: str = ""
    formatted_address: str = ""
    country: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    lat: float | None = None
    lng: float | None = None
    coordinate_system: str = ""
    precision: str = ""
    confidence: float = 0.0
    raw_response: dict[str, Any] | None = None


def institution_similarity(query: str, result: str) -> float:
    left, right = normalize_institution(query), normalize_institution(result)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right)) * 0.3 + 0.7
    return SequenceMatcher(None, left, right).ratio()


def _location_pair(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, str) and "," in value:
        lng, lat = value.split(",", 1)
        return float(lat), float(lng)
    if isinstance(value, dict):
        return float(value.get("lat")), float(value.get("lng"))
    return None, None


def query_amap(
    query: str, city_hint: str, api_key: str, session: requests.Session
) -> GeoCandidate | None:
    institution, department = split_institution_department(query)
    if not institution:
        return None
    api_key = api_key or configured_value("AMAP_API_KEY", "maps", "amap_api_key")
    base_url = api_endpoint("AMAP_BASE_URL", "amap_base_url")
    if not api_key or not base_url:
        warn_missing("Amap", "AMAP_API_KEY and AMAP_BASE_URL")
        return None
    response = session.get(
        f"{base_url}/place/text",
        params={"key": api_key, "keywords": institution, "city": city_hint,
                "citylimit": "false", "offset": 10, "output": "JSON"},
        timeout=20,
    )
    payload = response.json()
    candidates = []
    for poi in payload.get("pois") or []:
        lat, lng = _location_pair(poi.get("location"))
        city = str(poi.get("cityname") or "")
        similarity = institution_similarity(institution, str(poi.get("name") or ""))
        city_score = 1.0 if city_hint and normalize_place(city) == normalize_place(city_hint) else 0.0
        confidence = min(1.0, similarity * 0.8 + city_score * 0.2)
        candidates.append((confidence, poi, lat, lng))
    if not candidates:
        return None
    confidence, poi, lat, lng = max(candidates, key=lambda item: item[0])
    return GeoCandidate(
        provider="amap", query_text=query, institution_query=institution,
        department_query=department, result_name=str(poi.get("name") or ""),
        formatted_address=str(poi.get("address") or ""), country="China",
        province=str(poi.get("pname") or ""), city=str(poi.get("cityname") or ""),
        district=str(poi.get("adname") or ""), lat=lat, lng=lng,
        coordinate_system="GCJ-02", precision="poi", confidence=confidence,
        raw_response={key: poi.get(key) for key in
                      ("id", "name", "type", "address", "pname", "cityname", "adname", "location")},
    )


def query_baidu(
    query: str, city_hint: str, api_key: str, session: requests.Session
) -> GeoCandidate | None:
    institution, department = split_institution_department(query)
    api_key = api_key or baidu_api_key()
    base_url = api_endpoint("BAIDU_MAP_BASE_URL", "baidu_map_base_url")
    if not institution:
        return None
    if not api_key or not base_url:
        warn_missing("Baidu Maps", "BAIDU_MAP_AK and BAIDU_MAP_BASE_URL")
        return None
    response = session.get(
        f"{base_url}/place/v2/search",
        params={"ak": api_key, "query": institution, "region": city_hint or "全国",
                "city_limit": "false", "scope": 2, "output": "json"},
        timeout=20,
    )
    payload = response.json()
    candidates = []
    for poi in payload.get("results") or []:
        lat, lng = _location_pair(poi.get("location"))
        city = str(poi.get("city") or "")
        similarity = institution_similarity(institution, str(poi.get("name") or ""))
        city_score = 1.0 if city_hint and normalize_place(city) == normalize_place(city_hint) else 0.0
        candidates.append((min(1.0, similarity * 0.8 + city_score * 0.2), poi, lat, lng))
    if not candidates:
        return None
    confidence, poi, lat, lng = max(candidates, key=lambda item: item[0])
    detail = poi.get("detail_info") or {}
    return GeoCandidate(
        provider="baidu", query_text=query, institution_query=institution,
        department_query=department, result_name=str(poi.get("name") or ""),
        formatted_address=str(poi.get("address") or ""), country="China",
        province=str(poi.get("province") or ""), city=str(poi.get("city") or ""),
        district=str(poi.get("area") or ""), lat=lat, lng=lng,
        coordinate_system="BD-09", precision="poi", confidence=confidence,
        raw_response={"uid": poi.get("uid"), "name": poi.get("name"),
                      "address": poi.get("address"), "province": poi.get("province"),
                      "city": poi.get("city"), "area": poi.get("area"),
                      "location": poi.get("location"), "tag": detail.get("tag")},
    )


def query_nominatim(query: str, city_hint: str, session: requests.Session) -> GeoCandidate | None:
    institution, department = split_institution_department(query)
    if not institution:
        return None
    base_url = api_endpoint("NOMINATIM_BASE_URL", "nominatim_base_url")
    if not base_url:
        warn_missing("Nominatim", "NOMINATIM_BASE_URL")
        return None
    response = session.get(
        f"{base_url}/search",
        params={"q": f"{institution}, {city_hint}, China" if city_hint else f"{institution}, China",
                "format": "jsonv2", "addressdetails": 1, "limit": 5},
        headers={"User-Agent": contact_user_agent()},
        timeout=30,
    )
    candidates = []
    for poi in response.json() or []:
        address = poi.get("address") or {}
        city = str(address.get("city") or address.get("town") or address.get("county") or "")
        similarity = institution_similarity(institution, str(poi.get("name") or poi.get("display_name") or ""))
        city_score = 1.0 if city_hint and normalize_place(city) == normalize_place(city_hint) else 0.0
        candidates.append((min(1.0, similarity * 0.8 + city_score * 0.2), poi, address, city))
    if not candidates:
        return None
    confidence, poi, address, city = max(candidates, key=lambda item: item[0])
    return GeoCandidate(
        provider="nominatim", query_text=query, institution_query=institution,
        department_query=department, result_name=str(poi.get("name") or ""),
        formatted_address=str(poi.get("display_name") or ""),
        country=str(address.get("country") or "China"),
        province=str(address.get("state") or ""), city=city,
        district=str(address.get("suburb") or address.get("district") or ""),
        lat=float(poi["lat"]), lng=float(poi["lon"]), coordinate_system="WGS-84",
        precision=str(poi.get("type") or "place"), confidence=confidence,
        raw_response={key: poi.get(key) for key in
                      ("place_id", "name", "display_name", "lat", "lon", "type", "address")},
    )


def providers_agree(first: GeoCandidate, second: GeoCandidate) -> bool:
    return bool(
        first.city and second.city
        and normalize_place(first.city) == normalize_place(second.city)
        and first.confidence >= 0.65 and second.confidence >= 0.55
    )


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def baidu_api_key() -> str:
    return configured_value("BAIDU_MAP_AK", "maps", "baidu_map_ak")
