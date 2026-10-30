from paper_library.affiliation_normalizer import match_cn_to_en, match_cn_to_en_fuzzy


def test_pinyin_match_accepts_publisher_unicode_hyphens():
    assert match_cn_to_en("单雪峰", "Xue‐Feng Shan")
    assert match_cn_to_en("吕秋辰", "Qiu‑Chen Lü")


def test_pinyin_match_accepts_given_and_family_name_order():
    assert match_cn_to_en("单雪峰", "Shan, Xue-Feng")


def test_pinyin_match_rejects_different_chinese_name():
    assert not match_cn_to_en("吕秋辰", "Xue-Feng Shan")


def test_pinyin_match_handles_polyphonic_surnames_and_umlaut():
    assert match_cn_to_en("翟星辰", "Xingchen Zhai")
    assert match_cn_to_en("单雪峰", "Xuefeng Shan")
    assert match_cn_to_en("吕秋辰", "Qiuchen Lü")
    assert match_cn_to_en("阚云舟", "Yunzhou Kan")
    assert match_cn_to_en("卜星河", "Xinghe Bu")
    assert match_cn_to_en("李丽", "Li Li")


def test_fuzzy_pinyin_match_accepts_one_publisher_typo():
    assert match_cn_to_en_fuzzy("翟星辰", "Xingchn Zhai")
    assert not match_cn_to_en_fuzzy("翟星辰", "Wrong Example")


def test_fuzzy_pinyin_match_accepts_initial_and_adopted_given_name():
    assert match_cn_to_en_fuzzy("牛传欣", "Chuanxin M. Niu")
    assert match_cn_to_en_fuzzy("单雪峰", "Christina Xue-Feng Shan")
    assert match_cn_to_en_fuzzy("单雪峰", "Jack Xuefeng Shan")
    assert match_cn_to_en_fuzzy("高星越", "Xing-Yue Gao")
