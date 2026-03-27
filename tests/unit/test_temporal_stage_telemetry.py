from temporal_workflows.workflows.file_processing import (
    FileProcessingResult,
    FileProcessingWorkflow,
)


def test_stage_summary_identifies_dominant_stage():
    result = FileProcessingResult(
        status="completed",
        download_ms=120,
        parse_ms=950,
        ingest_ms=430,
        notify_ms=80,
    )

    stage_ms, dominant_stage, dominant_pct = FileProcessingWorkflow._summarize_stage_timings(result)

    assert stage_ms["parse"] == 950
    assert dominant_stage == "parse"
    assert dominant_pct > 50.0


def test_optimization_hint_is_stage_specific():
    parse_hint = FileProcessingWorkflow._optimization_hint_for_stage("parse")
    ingest_hint = FileProcessingWorkflow._optimization_hint_for_stage("ingest")

    assert "OCR" in parse_hint or "MarkItDown" in parse_hint
    assert "splitter" in ingest_hint or "embedding" in ingest_hint
