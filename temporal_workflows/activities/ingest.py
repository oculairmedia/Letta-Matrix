import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from temporalio import activity

from .common import IngestError

HAYHOOKS_INGEST_URL = os.getenv(
    "HAYHOOKS_INGEST_URL", "http://192.168.50.90:1416/ingest_document/run"
)


@dataclass
class IngestInput:
    text: str
    filename: str
    room_id: str
    sender: str


@dataclass
class IngestResult:
    success: bool
    chunks_stored: int = 0
    duration_ms: int = 0
    error: Optional[str] = None


@activity.defn
async def ingest_to_haystack(input: IngestInput) -> IngestResult:
    start = time.monotonic()
    activity.logger.info(
        f"Ingesting {input.filename} ({len(input.text)} chars) to Haystack"
    )

    payload = {
        "text": input.text,
        "filename": input.filename,
        "room_id": input.room_id,
        "sender": input.sender,
    }

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(HAYHOOKS_INGEST_URL, json=payload)

            if response.status_code != 200:
                raise IngestError(
                    f"Hayhooks HTTP {response.status_code} for {input.filename}: "
                    f"{response.text[:500]}"
                )

            result = response.json()

            result_data = result
            if isinstance(result.get("result"), str):
                result_data = json.loads(result["result"])

            status = result_data.get("status", "")
            if status == "ok":
                chunks = result_data.get("chunks_stored", 0)
                elapsed = int((time.monotonic() - start) * 1000)
                activity.logger.info(
                    f"Ingested {input.filename}: {chunks} chunks stored, {elapsed}ms"
                )
                return IngestResult(
                    success=True, chunks_stored=chunks, duration_ms=elapsed
                )

            detail = result_data.get("detail", "Unknown error")
            raise IngestError(f"Hayhooks ingest error for {input.filename}: {detail}")

    except IngestError:
        raise
    except httpx.TimeoutException as e:
        raise IngestError(f"Hayhooks timeout for {input.filename}: {e}") from e
    except Exception as e:
        raise IngestError(f"Unexpected error ingesting {input.filename}: {e}") from e
