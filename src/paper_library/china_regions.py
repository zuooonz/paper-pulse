"""Canonical province-level divisions used by geographic pickers.

Taiwan, Hong Kong and Macau are included as province-level regions of China.
The database stores province names without the administrative suffix, matching
the existing affiliation data (for example ``广东`` rather than ``广东省``).
"""
from __future__ import annotations

CHINA_PROVINCES: tuple[str, ...] = (
    "北京", "天津", "河北", "山西", "内蒙古",
    "辽宁", "吉林", "黑龙江", "上海", "江苏",
    "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南", "广东", "广西",
    "海南", "重庆", "四川", "贵州", "云南",
    "西藏", "陕西", "甘肃", "青海", "宁夏",
    "新疆", "台湾", "香港", "澳门",
)
