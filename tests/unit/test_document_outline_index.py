from src.core.document_outline_index import (
    _extract_key_topics,
    _extract_sections,
    _infer_document_type,
    build_outline_record,
    get_outline_overview,
    list_outline_records,
    upsert_outline_record,
)


def test_extract_sections_detects_markdown_and_numbered_headings():
    text = """# Title

Some intro

1 Scope
1.1 Details
"""

    sections, heading_found = _extract_sections(text)

    assert heading_found is True
    assert len(sections) == 3
    assert sections[0]["title"] == "Title"
    assert sections[0]["level"] == 1
    assert sections[1]["title"] == "Scope"
    assert sections[1]["level"] == 1
    assert sections[2]["title"] == "Details"
    assert sections[2]["level"] == 2


def test_extract_sections_falls_back_to_paragraph_titles():
    text = """First paragraph starts here and has details.

Second paragraph with more details.
"""

    sections, heading_found = _extract_sections(text)

    assert heading_found is False
    assert len(sections) == 2
    assert sections[0]["line"] is None
    assert sections[1]["line"] is None


def test_extract_key_topics_filters_stop_words_and_sorts_frequency():
    text = """The document and section discuss matrix matrix routing routing routing and bridge.
    This report covers bridge and matrix.
    """

    topics = _extract_key_topics(text, max_topics=3)

    assert "routing" in topics
    assert "matrix" in topics
    assert "document" not in topics
    assert "section" not in topics


def test_infer_document_type_prefers_filename_extension_then_text_signals():
    assert _infer_document_type("slides.pptx", "anything") == "presentation"
    assert _infer_document_type("", "Invoice amount due this month") == "invoice"
    assert _infer_document_type("", "Table of Contents and chapter 1") == "report"
    assert _infer_document_type("notes.txt", "plain text") == "text_document"


def test_build_record_and_overview_graceful_fallback_when_no_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_OUTLINE_INDEX_PATH", str(tmp_path / "outline.json"))

    record = build_outline_record(
        document_id="!room:test:$doc1",
        filename="notes.txt",
        room_id="!room:test",
        sender="@user:test",
        event_id="$evt1",
        text="paragraph one\n\nparagraph two",
        page_count=1,
        was_ocr=False,
    )
    upsert_outline_record(record)

    listed = list_outline_records(room_id="!room:test")
    assert len(listed) == 1
    assert listed[0]["document_id"] == "!room:test:$doc1"

    overview = get_outline_overview(room_id="!room:test")
    assert overview is not None
    assert overview["document_id"] == "!room:test:$doc1"
    assert overview["section_count"] >= 1
    assert overview["claims"]
    assert overview["citations"]


def test_get_outline_overview_returns_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_OUTLINE_INDEX_PATH", str(tmp_path / "outline-empty.json"))
    assert get_outline_overview(room_id="!missing:test") is None
