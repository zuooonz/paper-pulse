from paper_library.tag_enrichment import (
    DISEASE_GROUPS,
    DISEASE_PATTERNS,
    MULTIMODAL_ALIASES,
    MULTIMODAL_METHOD_GROUPS,
    PARADIGM_ALIASES,
    PARADIGM_GROUPS,
    RESEARCH_TOPIC_ALIASES,
    RESEARCH_TOPIC_GROUPS,
    STUDY_DESIGN_GROUPS,
    STUDY_DESIGN_PATTERNS,
    classify_multimodal_methods,
    classify_paradigms,
    classify_research_topics,
    classify_subject_diseases,
    extract_study_design,
    extract_subject_age_mean,
    extract_subject_disease,
    extract_subject_diseases,
    normalize_multimodal_techniques,
    normalize_paradigm_selection,
    normalize_paradigms,
)


def test_presentation_groups_cover_each_configured_catalog_once():
    def flattened(groups):
        values = [value for group in groups.values() for value in group]
        assert len(values) == len(set(values))
        return set(values)

    assert flattened(RESEARCH_TOPIC_GROUPS) == set(RESEARCH_TOPIC_ALIASES)
    assert flattened(DISEASE_GROUPS) == {label for label, _ in DISEASE_PATTERNS}
    assert flattened(STUDY_DESIGN_GROUPS) == {label for label, _ in STUDY_DESIGN_PATTERNS}
    assert flattened(PARADIGM_GROUPS) == set(PARADIGM_ALIASES)
    assert flattened(MULTIMODAL_METHOD_GROUPS) == set(MULTIMODAL_ALIASES)


def test_subject_classification_uses_configured_general_groups():
    assert extract_subject_disease(["神经退行性疾病患者"]) == "神经退行性疾病"
    assert extract_subject_disease(None, "Participants with cancer") == "肿瘤"
    assert extract_subject_disease(["健康成年人"]) is None
    assert extract_subject_diseases(
        ["心血管疾病与肌骨疾病患者"],
        title="Quantitative MRI in cardiovascular and musculoskeletal disease",
    ) == ["心血管疾病", "肌骨疾病"]


def test_subject_disease_tags_require_supporting_text():
    assert extract_subject_diseases(None, None, tags=["肿瘤"]) == []
    evidence = classify_subject_diseases(
        None,
        "The study recruited 15 patients with cancer.",
        tags=["肿瘤"],
    )
    assert evidence == {"肿瘤": ["abstract", "tag"]}


def test_age_requires_explicit_mean_wording():
    assert extract_subject_age_mean("The mean age was 64.27 ± 5.1 years.") == 64.3
    assert extract_subject_age_mean("受试者平均年龄为7.6岁。") == 7.6
    assert extract_subject_age_mean("Participants were aged 20-70 years.") is None
    assert extract_subject_age_mean("The mean age was 145 years.") is None


def test_study_design_uses_configured_design_terms():
    assert extract_study_design("A cross-sectional study", None) == "横断面"
    assert extract_study_design(None, "A methodological validation was performed") == "方法学验证"
    assert extract_study_design("Quantitative imaging of tissue", "Forty adults participated") is None


def test_existing_values_normalize_through_configured_aliases():
    assert normalize_paradigms(["resting-state", "task-based"]) == ["静息态扫描", "任务态"]
    assert normalize_multimodal_techniques(["positron emission tomography", "脑电"]) == ["PET", "EEG"]
    assert normalize_paradigm_selection(["resting-state", "/", "custom acquisition protocol"]) == [
        "静息态扫描",
        "custom acquisition protocol",
    ]


def test_topic_classification_uses_configured_topics_only():
    assert classify_research_topics(None, tags=["unconfigured value"]) == {}
    assert classify_research_topics(
        ["neurodegenerative disease", "quantitative imaging"],
        title="Quantitative imaging in neurodegenerative disease",
    ) == {
        "神经退行性疾病": ["existing", "title"],
        "方法学": ["existing", "title"],
    }


def test_paradigm_classification_combines_structured_and_text_evidence():
    evidence = classify_paradigms(
        ["resting-state"],
        abstract="A task-based imaging protocol was used for quantitative MRI.",
    )
    assert evidence == {
        "静息态扫描": ["existing"],
        "任务态": ["abstract"],
    }


def test_multimodal_classification_uses_configured_modality_terms():
    evidence = classify_multimodal_methods(
        ["PET"],
        title="PET and EEG acquisition for quantitative imaging",
        tags=["PET"],
    )
    assert evidence == {"PET": ["existing", "tag", "title"], "EEG": ["title"]}
