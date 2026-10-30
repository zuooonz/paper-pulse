import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from core.quality_scorer import score_draft

SAMPLE = """
## 研究背景
本研究探讨 qMRI 在脑卒中康复研究中的应用，填补了 XX 领域空白。

## 方法设计
共纳入 **45 名** 受试者，任务范式为静息态扫描，采用 T1/T2 定量成像方案。
信号处理使用T1 mapping 参数估计后采用 GLM 分析。

## 核心结果
康复组 vs 对照组：AUC=0.87，p<0.001，效应量 Cohen d=1.2。
激活区域集中于 M1/SMA，F(1,43)=18.4，p<0.001。

## 学术贡献
首次系统评估 qMRI 指标在急性卒中研究中的可行性，局限性：样本量偏小。

## 参考信息
标题：XXX | 期刊：Brain | 2024 | DOI: 10.xxx
"""

def test_score_returns_tuple():
    total, dims = score_draft(SAMPLE)
    assert isinstance(total, int)
    assert 0 <= total <= 100
    assert set(dims.keys()) == {"length", "structure", "stats", "banned", "cn_ratio"}

def test_good_content_scores_high():
    total, _ = score_draft(SAMPLE)
    assert total >= 60, f"Expected >= 60, got {total}"

def test_empty_content_scores_low():
    total, _ = score_draft("")
    assert total <= 20

def test_banned_word_reduces_score():
    bad = SAMPLE + "\n虚构数据杜撰内容"
    total_bad, dims_bad = score_draft(bad)
    total_good, dims_good = score_draft(SAMPLE)
    assert dims_bad["banned"] < dims_good["banned"]
