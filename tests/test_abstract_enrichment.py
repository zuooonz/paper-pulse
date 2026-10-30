from types import SimpleNamespace

from paper_library.abstract_enrichment import (
    enrich_missing_abstract,
    extract_abstract_from_fulltext,
    extract_crossref_abstract,
    translate_abstract_batch_to_chinese,
    translate_abstract_to_chinese,
)


def _paper(**values):
    defaults = {
        "doi": "10.1000/example",
        "doi_meta": None,
        "pmid": None,
        "abstract": None,
        "abstract_zh": None,
        "conclusions_zh": None,
        "highlights_zh": None,
        "extracted_text": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_extract_crossref_abstract_cleans_jats_markup():
    payload = {"raw": {"abstract": "<jats:p>Background: A &amp; B with enough detail.</jats:p>"}}
    assert extract_crossref_abstract(payload) == "Background: A & B with enough detail."


def test_enrich_retries_crossref_when_cached_metadata_has_no_abstract():
    paper = _paper(doi_meta={"title": ["Example"]})

    def fetcher(doi, **kwargs):
        assert doi == "10.1000/example"
        assert kwargs["structured"] is True
        return {
            "abstract": "This is a sufficiently long authoritative abstract from Crossref.",
            "raw": {"title": ["Example"], "abstract": "raw abstract"},
        }

    result = enrich_missing_abstract(paper, fetcher=fetcher)

    assert result.changed is True
    assert result.source == "crossref"
    assert paper.abstract.startswith("This is a sufficiently long")
    assert paper.doi_meta["title"] == ["Example"]


def test_enrich_falls_back_to_explicit_fulltext_after_crossref_failure():
    paper = _paper(
        extracted_text=(
            "Paper title\nAuthors\nABSTRACT\n"
            "This study evaluates a robust workflow for recovering original abstracts "
            "from full text while preserving the reported evidence and terminology.\n\n"
            "Keywords: workflow, metadata\n1 Introduction\nBody"
        )
    )

    result = enrich_missing_abstract(
        paper,
        fetcher=lambda *args, **kwargs: {"_error": "temporary timeout"},
        pubmed_fetcher=lambda *args, **kwargs: None,
    )

    assert result.changed is True
    assert result.source == "fulltext"
    assert result.error == "temporary timeout"
    assert paper.abstract.startswith("This study evaluates")


def test_enrich_uses_pubmed_before_fulltext():
    paper = _paper(
        pmid="12345.0",
        extracted_text="ABSTRACT\nFull text fallback that should not be selected.\nKeywords: test",
    )

    result = enrich_missing_abstract(
        paper,
        fetcher=lambda *args, **kwargs: {"_error": "not found"},
        pubmed_fetcher=lambda pmid, **kwargs: (
            "This is a sufficiently long authoritative abstract returned by PubMed."
        ),
    )

    assert result.source == "pubmed"
    assert paper.abstract.endswith("PubMed.")


def test_fulltext_extractor_requires_explicit_heading():
    text = "Introduction\n" + ("This article discusses abstract concepts. " * 20)
    assert extract_abstract_from_fulltext(text) is None


def test_fulltext_extractor_skips_table_of_contents_abstract_entry():
    text = (
        "Contents\nAbstract ................................................ iv\n"
        "Introduction ............................................ 1\n"
        "\nAbstract\n"
        "This is the real abstract section. It contains enough substantive "
        "content to pass the extractor length check while the earlier table "
        "of contents entry must be ignored completely.\n\n"
        "Keywords: extraction, thesis"
    )

    assert extract_abstract_from_fulltext(text).startswith("This is the real abstract")


def test_fulltext_extractor_accepts_unlabelled_structured_abstract():
    text = (
        "Title and authors\nBackground: The clinical problem remains unresolved.\n"
        "Purpose: This study evaluates a deterministic extraction method.\n"
        "Methods: We evaluated the method on representative full text documents.\n"
        "Results: The method recovered the complete summary without body text.\n"
        "Conclusion: The guarded fallback was reliable.\nKeywords: extraction\n"
        "Introduction\nBody"
    )

    result = extract_abstract_from_fulltext(text)
    assert result.startswith("Background:")
    assert result.endswith("The guarded fallback was reliable.")


def test_fulltext_extractor_decodes_shifted_pdf_font_abstract():
    plain = (
        "Abstract\nThis shifted-font abstract contains enough content for safe "
        "recovery and confirms that the reversible character mapping works.\n"
        "It also includes a second substantive sentence so the synthetic full "
        "text is long enough to represent a realistic extracted document.\n"
        "1 Introduction\nBody"
    )
    encoded = "".join(
        char if char.isspace() else chr(ord(char) - 29)
        for char in plain
    )

    result = extract_abstract_from_fulltext(encoded)
    assert result.startswith("This shifted-font abstract")


def test_fulltext_extractor_accepts_guarded_aiaa_front_matter_summary():
    text = (
        "Paper title\nAuthors\nDepartment of Engineering, Example University\n"
        "This study evaluates an aviation interface using objective measurements. "
        "The methods combine several sensors in a controlled experiment. The results "
        "show improved situational awareness and support the proposed design for "
        "future cockpit interaction studies. Additional details make this a complete "
        "front-matter summary rather than a title or affiliation.\n"
        "I. Introduction\nBody"
    )

    result = extract_abstract_from_fulltext(text)
    assert result.startswith("This study evaluates")


class _TranslationClient:
    def chat(self, model, messages, **kwargs):
        assert model == "example-chat-model"
        assert "原文摘要" in messages[1]["content"]
        assert kwargs["response_format"] == "json_object"
        return (
            '{"abstract_zh":"这是一段忠实的中文摘要翻译，保留原文研究结论与专业术语。",'
            '"conclusions_zh":"研究结果支持该方法有效，并为后续临床研究提供了可靠依据。",'
            '"highlights_zh":["完整保留原文研究数据和专业术语",'
            '"清晰呈现主要研究方法与核心结果","为后续相关研究提供明确参考"]}'
        )


def test_translate_abstract_uses_llm_and_preserves_original():
    original = "This is a sufficiently long original abstract for faithful translation."
    paper = _paper(abstract=original)

    result = translate_abstract_to_chinese(paper, _TranslationClient())

    assert result.changed is True
    assert result.source == "llm_chinese_package"
    assert paper.abstract == original
    assert paper.abstract_zh.startswith("这是一段忠实")
    assert paper.conclusions_zh.startswith("研究结果")
    assert len(paper.highlights_zh) == 3


def test_chinese_original_is_copied_without_llm_call():
    paper = _paper(
        abstract="这是一段已经存在于原文字段中的中文摘要，内容足够长，可以直接用于中文展示。",
        conclusions_zh="这是已经存在且无需重新生成的中文结论内容。",
        highlights_zh=["这是已经存在的第一条研究亮点", "这是已经存在的第二条研究亮点"],
    )

    result = translate_abstract_to_chinese(paper, object())

    assert result.source == "original_is_chinese"
    assert paper.abstract_zh == paper.abstract


class _BatchTranslationClient:
    def chat(self, model, messages, **kwargs):
        assert model == "example-chat-model"
        return (
            '{"papers":['
            '{"id":1,"abstract_zh":"第一篇论文的忠实中文摘要翻译，内容完整且长度有效。",'
            '"conclusions_zh":"第一篇论文的核心研究结论完整有效且忠于原文。",'
            '"highlights_zh":["第一篇论文的第一项重要研究亮点","第一篇论文的第二项重要研究亮点"]},'
            '{"id":2,"abstract_zh":"第二篇论文的忠实中文摘要翻译，内容完整且长度有效。",'
            '"conclusions_zh":"第二篇论文的核心研究结论完整有效且忠于原文。",'
            '"highlights_zh":["第二篇论文的第一项重要研究亮点","第二篇论文的第二项重要研究亮点"]}'
            ']}'
        )


def test_batch_translation_maps_results_by_paper_id():
    papers = [
        _paper(id=1, abstract="The first sufficiently long original abstract for translation."),
        _paper(id=2, abstract="The second sufficiently long original abstract for translation."),
    ]

    results = translate_abstract_batch_to_chinese(papers, _BatchTranslationClient())

    assert results[1].changed is True
    assert results[2].changed is True
    assert papers[0].abstract_zh.startswith("第一篇")
    assert papers[1].abstract_zh.startswith("第二篇")
    assert papers[0].conclusions_zh.startswith("第一篇")
    assert len(papers[1].highlights_zh) == 2


def test_existing_translation_is_preserved_while_missing_summary_is_generated():
    paper = _paper(
        abstract="This is a sufficiently long original abstract for package generation.",
        abstract_zh="这是已经审核过的中文摘要，不应被新的模型响应覆盖。",
    )

    result = translate_abstract_to_chinese(paper, _TranslationClient())

    assert result.changed is True
    assert paper.abstract_zh == "这是已经审核过的中文摘要，不应被新的模型响应覆盖。"
    assert paper.conclusions_zh
    assert paper.highlights_zh
