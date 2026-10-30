from paper_library.corresponding_affiliation import (
    CorrespondingAffiliation,
    choose_primary_affiliation,
    extract_department,
    extract_institution_type,
    normalize_author_geography,
    normalize_department,
)


def test_institution_type_prefers_hospital_for_affiliated_hospital():
    assert extract_institution_type("山东大学齐鲁医院康复科") == "临床"


def test_institution_type_uses_checked_chinese_affiliation():
    assert extract_institution_type("北京航空航天大学生物与医学工程学院") == "科研"
    assert extract_institution_type("国家康复辅具研究中心") == "科研"
    assert extract_institution_type("杭州泛康诊所，杭州泛康健康管理有限公司") == "临床"
    assert extract_institution_type("陆军特色医学中心 康复理疗科") == "临床"


def test_institution_type_uses_primary_checked_unit_not_later_hospital_mention():
    affiliation = "云南师范大学信息科学与技术学院，玉溪市第二人民医院精神卫生重点实验室"
    assert extract_institution_type(affiliation) == "科研"
    assert extract_institution_type("北京理工大学医学技术学院", "解放军总医院神经内科") == "科研"


def test_department_extracts_clinical_and_academic_units():
    assert extract_department("山东大学齐鲁医院康复医学科") == "康复医学科"
    assert extract_department("北京航空航天大学生物与医学工程学院") == "生物医学工程"
    assert extract_department("成都医学院公共卫生学院") == "公共卫生"


def test_department_ignores_institution_and_system_false_positives():
    assert extract_department("中国科学院深圳先进技术研究院") is None
    assert extract_department("虚拟现实技术与系统国家重点实验室") is None
    assert extract_department("浙江大学医学院附属妇产科医院") == "妇产科"


def test_normalize_clinical_department_aliases():
    assert normalize_department("康复科", "医院") == "康复医学科"
    assert normalize_department("Physical Medicine and Rehabilitation", "医院") == "康复医学科"
    assert normalize_department("老年精神科", "医院") == "精神心理科"
    assert normalize_department("耳鼻喉科", "医院") == "耳鼻咽喉头颈外科"


def test_normalize_academic_department_aliases():
    assert normalize_department("心理与认知科学系", "高校") == "心理学"
    assert normalize_department("生物医学工程学系", "高校") == "生物医学工程"
    assert normalize_department("机械与汽车工程学院", "高校") == "机械与机电工程"
    assert normalize_department("人工智能与数据科学学院", "高校") == "计算机与人工智能"


def test_normalize_department_uses_closed_research_vocabulary():
    assert normalize_department("食品科学技术学院", "科研") == "食品与生物工程"
    assert normalize_department("无法识别学院", "科研") is None


def test_hospital_department_rejects_academic_organizations():
    assert normalize_department("信息科学与技术学院", "医院") is None
    assert normalize_department("Life Sciences and Technology", "医院") is None
    assert normalize_department("康复医学院", "医院") is None


def test_hospital_department_prefers_clinical_unit_from_multiple_affiliations():
    affiliation = (
        "School of Life Sciences and Technology, Xi'an Jiaotong University; "
        "Department of Rehabilitation, First Affiliated Hospital of Xi'an Jiaotong University"
    )
    assert extract_department("西安交通大学第一附属医院", affiliation) == "康复医学科"


def test_department_rejects_whole_institution_and_generic_fragments():
    assert extract_department("齐鲁师范学院", "Qilu Normal University") is None
    assert normalize_department("学院", "科研") is None


def test_mixed_neurology_school_maps_to_stable_research_discipline():
    affiliation = "天津医科大学临床医学院（神经内外科及神经康复临床学院）"
    assert extract_department(affiliation) == "临床医学"


def test_primary_affiliation_prefers_canonical_geography_then_author_order():
    rows = [
        CorrespondingAffiliation(1, 0, "甲大学心理学院", None, None, None, None),
        CorrespondingAffiliation(2, 1, "乙医院康复科", None, "China", "广东省", "广州市"),
    ]
    assert choose_primary_affiliation(rows).author_id == 2


def test_geography_corrects_explicit_foreign_affiliation_mismatch():
    geo = normalize_author_geography(
        "美国加利福尼亚州圣何塞市医疗中心儿科", None,
        "China", None, "台州市",
    )
    assert (geo.country, geo.province, geo.city) == ("United States", None, None)


def test_geography_keeps_checked_mainland_location():
    geo = normalize_author_geography(
        "山东大学齐鲁医院康复医学科", None,
        "China", "山东省", "济南市",
    )
    assert (geo.country, geo.province, geo.city) == ("China", "山东省", "济南市")


def test_geography_does_not_misclassify_joint_mainland_institution():
    geo = normalize_author_geography(
        "山东大学-南洋理工大学人工智能国际联合研究院", None,
        "China", "山东省", "济南市",
    )
    assert geo.country == "China"


def test_geography_uses_checked_institution_before_historical_location():
    geo = normalize_author_geography(
        "南京医科大学第一附属医院康复医学科", None,
        "China", "天津", "天津",
        {"南京": ("江苏", 0.0, 0.0), "天津": ("天津", 0.0, 0.0)},
        {"南京医科大学": "南京"},
    )
    assert (geo.country, geo.province, geo.city) == ("China", "江苏省", "南京市")
