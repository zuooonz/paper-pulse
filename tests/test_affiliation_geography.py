from paper_library.affiliation_geography import (
    GeoCandidate,
    institution_similarity,
    providers_agree,
    split_institution_department,
)


def test_split_institution_from_department():
    assert split_institution_department("天津中医药大学第一附属医院康复科") == (
        "天津中医药大学第一附属医院", "康复科",
    )
    assert split_institution_department(
        "上海交通大学医学院附属上海儿童医学中心发育行为儿科"
    ) == ("上海交通大学医学院附属上海儿童医学中心", "发育行为儿科")


def test_provider_consensus_requires_city_and_name_confidence():
    amap = GeoCandidate(
        provider="amap", query_text="x", institution_query="天津市西青医院",
        department_query="", result_name="天津市西青医院", city="天津市",
        confidence=0.95,
    )
    baidu = GeoCandidate(
        provider="baidu", query_text="x", institution_query="天津市西青医院",
        department_query="", result_name="天津市西青医院", city="天津",
        confidence=0.92,
    )
    wrong = GeoCandidate(
        provider="baidu", query_text="x", institution_query="天津市西青医院",
        department_query="", result_name="同名医院", city="上海市", confidence=0.9,
    )
    assert institution_similarity("天津市西青医院", "天津市西青医院") == 1.0
    assert providers_agree(amap, baidu)
    assert not providers_agree(amap, wrong)
