"""规则打分：0-100，各维度 20 分"""
from __future__ import annotations
import re
from typing import Dict, Tuple

_BANNED = ["虚构", "杜撰", "编造", "点击查看", "限时优惠", "扫码领取"]
_SECTION_HEADERS = ["##"]
_STAT_PATTERN = re.compile(r'(\d+\.?\d*\s*%|p\s*[<=>]\s*0\.\d+|AUC|r\s*=\s*0\.\d+|F\(\d+)', re.I)
_CN_CHAR = re.compile(r'[\u4e00-\u9fff]')
_EN_WORD = re.compile(r'[a-zA-Z]{2,}')


def score_draft(content: str) -> Tuple[int, Dict[str, int]]:
    """返回 (total_score, dimensions)，total 0-100"""
    dims: Dict[str, int] = {}

    # 字数（20分）：800-2000字满分，线性衰减
    char_count = len(content.replace(' ', '').replace('\n', ''))
    if 800 <= char_count <= 2000:
        dims["length"] = 20
    elif char_count < 800:
        dims["length"] = max(0, int(char_count / 800 * 20))
    else:
        dims["length"] = max(0, 20 - int((char_count - 2000) / 200))

    # 结构完整性（20分）：含多个 ## 标题
    headers = re.findall(r'^##\s+\S', content, re.MULTILINE)
    dims["structure"] = min(20, len(headers) * 5)

    # 统计数据引用（20分）：含统计量（p值/AUC/百分比等）
    stats = _STAT_PATTERN.findall(content)
    dims["stats"] = min(20, len(stats) * 5)

    # 禁止词（20分）：含禁止词扣分
    banned_found = [w for w in _BANNED if w in content]
    dims["banned"] = max(0, 20 - len(banned_found) * 10)

    # 中文比例（20分）：中文字数 / (中文+英文单词) > 0.6 满分
    cn = len(_CN_CHAR.findall(content))
    en = len(_EN_WORD.findall(content))
    ratio = cn / (cn + en) if (cn + en) > 0 else 0
    dims["cn_ratio"] = 20 if ratio >= 0.6 else int(ratio / 0.6 * 20)

    total = sum(dims.values())
    return total, dims
