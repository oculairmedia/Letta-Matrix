import json
import os
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_lock = threading.Lock()


def _index_path() -> str:
    return os.getenv("DOCUMENT_OUTLINE_INDEX_PATH", "./matrix_client_data/document_outline_index.json")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_records() -> List[Dict[str, Any]]:
    path = _index_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_records(records: List[Dict[str, Any]]) -> None:
    path = _index_path()
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _extract_sections(text: str, max_sections: int = 80) -> tuple[List[Dict[str, Any]], bool]:
    sections: List[Dict[str, Any]] = []
    heading_found = False
    lines = [line.strip() for line in text.splitlines()]

    for i, line in enumerate(lines):
        if not line:
            continue

        markdown = re.match(r"^(#{1,6})\s+(.+)$", line)
        if markdown:
            heading_found = True
            sections.append(
                {
                    "order": len(sections) + 1,
                    "level": len(markdown.group(1)),
                    "title": markdown.group(2).strip()[:200],
                    "line": i + 1,
                }
            )
            if len(sections) >= max_sections:
                break
            continue

        numbered = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", line)
        if numbered:
            heading_found = True
            level = numbered.group(1).count(".") + 1
            sections.append(
                {
                    "order": len(sections) + 1,
                    "level": min(level, 6),
                    "title": numbered.group(2).strip()[:200],
                    "line": i + 1,
                }
            )
            if len(sections) >= max_sections:
                break

    if sections:
        return sections, heading_found

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for idx, paragraph in enumerate(paragraphs[: min(12, max_sections)], start=1):
        first_line = paragraph.splitlines()[0].strip()
        sections.append(
            {
                "order": idx,
                "level": 1,
                "title": first_line[:120],
                "line": None,
            }
        )

    return sections, heading_found


def _extract_key_topics(text: str, max_topics: int = 8) -> List[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "have",
        "will",
        "your",
        "you",
        "are",
        "was",
        "were",
        "into",
        "about",
        "there",
        "their",
        "they",
        "them",
        "then",
        "than",
        "when",
        "what",
        "where",
        "which",
        "document",
        "section",
    }
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    filtered = [t for t in tokens if t not in stop_words]
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(max_topics)]


def _infer_document_type(filename: str, text: str) -> str:
    lower_name = (filename or "").lower()
    if lower_name.endswith(".pdf"):
        return "pdf"
    if lower_name.endswith(".docx") or lower_name.endswith(".doc"):
        return "word_document"
    if lower_name.endswith(".pptx") or lower_name.endswith(".ppt"):
        return "presentation"
    if lower_name.endswith(".xlsx") or lower_name.endswith(".csv"):
        return "spreadsheet"

    lower_text = text.lower()
    if "invoice" in lower_text or "amount due" in lower_text:
        return "invoice"
    if "table of contents" in lower_text or "chapter" in lower_text:
        return "report"
    return "text_document"


def build_outline_record(
    *,
    document_id: str,
    filename: str,
    room_id: str,
    sender: str,
    event_id: str,
    text: str,
    page_count: Optional[int],
    was_ocr: bool,
) -> Dict[str, Any]:
    sections, heading_found = _extract_sections(text)
    return {
        "document_id": document_id,
        "filename": filename,
        "room_id": room_id,
        "sender": sender,
        "event_id": event_id,
        "page_count": page_count,
        "was_ocr": was_ocr,
        "char_count": len(text),
        "has_heading_signals": heading_found,
        "sections": sections,
        "key_topics": _extract_key_topics(text),
        "document_type": _infer_document_type(filename, text),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_outline_record(record: Dict[str, Any]) -> None:
    with _lock:
        records = _load_records()
        records = [r for r in records if r.get("document_id") != record.get("document_id")]
        records.append(record)
        records.sort(key=lambda r: r.get("ingested_at", ""), reverse=True)
        _save_records(records)


def list_outline_records(
    *,
    room_id: Optional[str] = None,
    filename: Optional[str] = None,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    with _lock:
        records = _load_records()

    filtered = records
    if document_id:
        filtered = [r for r in filtered if r.get("document_id") == document_id]
    if room_id:
        filtered = [r for r in filtered if r.get("room_id") == room_id]
    if filename:
        filename_lower = filename.lower()
        filtered = [r for r in filtered if str(r.get("filename", "")).lower() == filename_lower]
    return filtered


def get_outline_overview(
    *,
    room_id: Optional[str] = None,
    filename: Optional[str] = None,
    document_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    records = list_outline_records(room_id=room_id, filename=filename, document_id=document_id)
    if not records:
        return None

    record = records[0]
    sections = record.get("sections", [])
    citations = []
    for idx, section in enumerate(sections[:8], start=1):
        line_value = section.get("line")
        citations.append(
            {
                "chunk_id": f"{record.get('document_id')}:sec-{section.get('order', idx)}",
                "score": round(max(0.5, 1.0 - (idx - 1) * 0.08), 2),
                "excerpt": str(section.get("title", ""))[:240],
                "line": line_value,
                "source_coordinate": f"line:{line_value}" if line_value is not None else None,
            }
        )

    def citation_for(*positions: int) -> List[Dict[str, Any]]:
        selected = [citations[p] for p in positions if p < len(citations)]
        if selected:
            return selected
        fallback = {
            "chunk_id": f"{record.get('document_id')}:sec-0",
            "score": 0.5,
            "excerpt": str(record.get("filename", ""))[:240],
            "line": None,
            "source_coordinate": None,
        }
        return [fallback]

    key_topics = record.get("key_topics", [])
    claims = [
        {
            "claim": f"This document is likely a {record.get('document_type', 'text_document').replace('_', ' ')}.",
            "citations": citation_for(0),
        },
        {
            "claim": f"The document appears to cover {', '.join(key_topics[:3]) if key_topics else 'general topics'}.",
            "citations": citation_for(1, 2),
        },
        {
            "claim": f"The outline has {len(sections)} sections and heading signals are {'present' if record.get('has_heading_signals') else 'not available'}.",
            "citations": citation_for(0, 3),
        },
    ]

    return {
        "document_id": record.get("document_id"),
        "filename": record.get("filename"),
        "document_type": record.get("document_type"),
        "key_topics": record.get("key_topics", []),
        "char_count": record.get("char_count", 0),
        "section_count": len(sections),
        "has_heading_signals": bool(record.get("has_heading_signals")),
        "graceful_fallback_used": not bool(record.get("has_heading_signals")),
        "sections": sections,
        "citations": citations,
        "claims": claims,
        "why_this_answer": "Claims are synthesized from section titles and extracted key topics. Each claim links to supporting outline chunks.",
        "ingested_at": record.get("ingested_at"),
    }
