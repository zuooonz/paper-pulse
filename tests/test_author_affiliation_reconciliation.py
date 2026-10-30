from paper_library.author_affiliation_reconciliation import (
    LocationCandidate,
    choose_location,
    normalize_affiliation,
    normalize_special_jurisdiction,
)


def _candidate(location_id, zh, en=None):
    return LocationCandidate(
        id=location_id, affiliation_zh=zh, canonical_name_en=en,
        country="China", province="江苏省", city="南京市",
        lat=32.0, lng=118.0,
    )


def test_normalize_affiliation_only_removes_formatting():
    assert normalize_affiliation("Huashan Hospital, Fudan-University") == (
        "huashanhospitalfudanuniversity"
    )


def test_choose_location_uses_explicit_chinese_institution():
    result = choose_location(
        "南京医科大学第一附属医院康复医学科", None,
        [_candidate(1, "天津市环湖医院"), _candidate(2, "南京医科大学第一附属医院")],
        current_location_id=1,
    )
    assert result.location_id == 2
    assert result.status == "relink"
    assert result.matched_field == "affiliation_zh"


def test_choose_location_keeps_matching_current_parent():
    result = choose_location(
        "广东省人民医院康复医学科", None,
        [_candidate(1, "广东省人民医院"), _candidate(2, "广东省人民医院康复医学科")],
        current_location_id=1,
    )
    assert result.location_id == 1
    assert result.status == "current_match"


def test_choose_location_can_use_publication_english_name():
    result = choose_location(
        None, "Department of Rehabilitation, Huashan Hospital Fudan University, China",
        [_candidate(3, "复旦大学附属华山医院", "Huashan Hospital Fudan University")],
    )
    assert result.location_id == 3
    assert result.matched_field == "affiliation_en"


def test_choose_location_refuses_equal_ambiguous_matches():
    result = choose_location(
        "澳门大学科技学院", None,
        [_candidate(4, "澳门大学科技学院"), _candidate(5, "澳门大学科技学院")],
    )
    assert result.location_id is None
    assert result.status == "ambiguous_match"


def test_choose_location_does_not_fuzzy_guess():
    result = choose_location(
        "南京医科大学第一附属医院", None,
        [_candidate(6, "南京大学医学院附属医院")],
    )
    assert result.location_id is None
    assert result.status == "no_explicit_match"


def test_choose_location_keeps_explicit_current_even_when_untrusted():
    current = LocationCandidate(
        id=7,
        affiliation_zh="Hangzhou International Innovation Institute, Beihang University",
        canonical_name_en=None,
        country="China", province="浙江省", city="杭州市",
        lat=30.0, lng=120.0, trusted=False,
    )
    parent = _candidate(8, "北京航空航天大学", "Beihang University")
    result = choose_location(
        "北京航空航天大学国际创新研究院",
        "Hangzhou International Innovation Institute, Beihang University, Hangzhou, China",
        [current, parent], current_location_id=7,
    )
    assert result.location_id == 7
    assert result.status == "current_match"


def test_choose_location_never_relinks_to_untrusted_candidate():
    untrusted = LocationCandidate(
        id=9, affiliation_zh="复旦大学附属华山医院", canonical_name_en=None,
        country="China", province="上海", city="上海",
        lat=31.0, lng=121.0, trusted=False,
    )
    result = choose_location("复旦大学附属华山医院康复医学科", None, [untrusted])
    assert result.location_id is None
    assert result.status == "no_explicit_match"


def test_macau_university_uses_institution_jurisdiction_not_provider_city():
    assert normalize_special_jurisdiction(
        "澳门大学科技学院", "China", "广东省", "珠海市",
    ) == ("Macau", "澳门特别行政区", "澳门")


def test_hong_kong_institution_has_filterable_country():
    assert normalize_special_jurisdiction(
        "香港理工大学", "China", "香港特别行政区", "香港",
    ) == ("Hong Kong", "香港特别行政区", "香港")
