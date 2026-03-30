import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.app import app
from src.core.document_outline_index import upsert_outline_record


def test_outline_endpoint_filters_multiple_documents_by_filename(tmp_path):
    index_path = tmp_path / "document_outline_index.json"
    with patch.dict(os.environ, {"DOCUMENT_OUTLINE_INDEX_PATH": str(index_path)}, clear=False):
        upsert_outline_record(
            {
                "document_id": "!room:test:$doc1",
                "filename": "project-plan.pdf",
                "room_id": "!room:test",
                "sender": "@user:test",
                "event_id": "$doc1",
                "page_count": 4,
                "was_ocr": False,
                "char_count": 900,
                "has_heading_signals": True,
                "sections": [{"order": 1, "level": 1, "title": "Executive Summary", "line": 1}],
                "key_topics": ["plan", "timeline"],
                "document_type": "pdf",
                "ingested_at": "2026-03-29T12:00:00+00:00",
            }
        )
        upsert_outline_record(
            {
                "document_id": "!room:test:$doc2",
                "filename": "release-notes.pdf",
                "room_id": "!room:test",
                "sender": "@user:test",
                "event_id": "$doc2",
                "page_count": 2,
                "was_ocr": False,
                "char_count": 450,
                "has_heading_signals": False,
                "sections": [{"order": 1, "level": 1, "title": "Section 1", "line": None}],
                "key_topics": ["release", "changes"],
                "document_type": "pdf",
                "ingested_at": "2026-03-29T12:01:00+00:00",
            }
        )

        client = TestClient(app)
        response = client.get("/documents/outline", params={"filename": "project-plan.pdf"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["success"] is True
        assert len(payload["outlines"]) == 1
        assert payload["outlines"][0]["filename"] == "project-plan.pdf"


def test_overview_endpoint_exposes_claim_to_citation_mapping(tmp_path):
    index_path = tmp_path / "document_outline_index.json"
    with patch.dict(os.environ, {"DOCUMENT_OUTLINE_INDEX_PATH": str(index_path)}, clear=False):
        upsert_outline_record(
            {
                "document_id": "!room:test:$doc-trace",
                "filename": "contract-notes.pdf",
                "room_id": "!room:test",
                "sender": "@user:test",
                "event_id": "$doc-trace",
                "page_count": 7,
                "was_ocr": False,
                "char_count": 2400,
                "has_heading_signals": True,
                "sections": [
                    {"order": 1, "level": 1, "title": "Scope", "line": 3},
                    {"order": 2, "level": 1, "title": "Payment Terms", "line": 14},
                ],
                "key_topics": ["scope", "payment", "timeline"],
                "document_type": "pdf",
                "ingested_at": "2026-03-29T12:03:00+00:00",
            }
        )

        client = TestClient(app)
        response = client.get("/documents/overview", params={"filename": "contract-notes.pdf"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["success"] is True
        overview = payload["overview"]
        assert overview["citations"]
        for citation in overview["citations"]:
            assert citation["chunk_id"].startswith("!room:test:$doc-trace:sec-")
            assert isinstance(citation["score"], float)
            assert "excerpt" in citation

        assert overview["claims"]
        for claim in overview["claims"]:
            assert claim["claim"]
            assert claim["citations"]
