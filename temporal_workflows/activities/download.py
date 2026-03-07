import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

import httpx
from temporalio import activity

from .common import DownloadError

MATRIX_HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://tuwunel:6167")
MATRIX_ACCESS_TOKEN = os.getenv("MATRIX_ACCESS_TOKEN", "")

PERSISTENT_DOCUMENTS_DIR = os.getenv("PERSISTENT_DOCUMENTS_DIR", "/app/documents")
_HASH_INDEX_FILENAME = ".hashes.json"
_HASH_LOCK_FILENAME = ".hashes.lock"

_EXT_MAP = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/xhtml+xml": ".xhtml",
    "application/epub+zip": ".epub",
    "text/calendar": ".ics",
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_index_paths() -> tuple[str, str]:
    idx_path = os.path.join(PERSISTENT_DOCUMENTS_DIR, _HASH_INDEX_FILENAME)
    lock_path = os.path.join(PERSISTENT_DOCUMENTS_DIR, _HASH_LOCK_FILENAME)
    return idx_path, lock_path


def _load_hash_index() -> dict:
    idx_path, _ = _hash_index_paths()
    if os.path.exists(idx_path):
        try:
            with open(idx_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_hash_index(index: dict) -> None:
    idx_path, _ = _hash_index_paths()
    tmp_path = idx_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp_path, idx_path)


def _mutate_hash_index_locked(mutate_fn):
    os.makedirs(PERSISTENT_DOCUMENTS_DIR, exist_ok=True)
    idx_path, lock_path = _hash_index_paths()

    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if os.path.exists(idx_path):
                try:
                    with open(idx_path, "r") as f:
                        index = json.load(f)
                except (json.JSONDecodeError, OSError):
                    index = {}
            else:
                index = {}

            result = mutate_fn(index)

            tmp_path = idx_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(index, f, indent=2)
            os.replace(tmp_path, idx_path)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _persist_file(temp_path: str, file_name: str, mxc_url: str) -> tuple[str, str, bool]:
    file_hash = ""
    try:
        file_hash = _sha256(temp_path)

        def _persist_under_lock(index: dict) -> tuple[str, str, bool]:
            if file_hash in index:
                existing = index[file_hash]
                import logging

                logging.getLogger(__name__).info(
                    f"Duplicate detected: {file_name} matches existing "
                    f"{existing.get('filename')} (hash={file_hash[:12]}...)"
                )
                return existing.get("persistent_path", ""), file_hash, True

            media_id = mxc_url.split("/")[-1] if mxc_url else "unknown"
            dest_dir = os.path.join(PERSISTENT_DOCUMENTS_DIR, media_id)
            os.makedirs(dest_dir, exist_ok=True)

            dest_path = os.path.join(dest_dir, file_name)
            shutil.copy2(temp_path, dest_path)

            index[file_hash] = {
                "filename": file_name,
                "mxc_url": mxc_url,
                "persistent_path": dest_path,
                "ts": time.time(),
            }
            return dest_path, file_hash, False

        return _mutate_hash_index_locked(_persist_under_lock)

    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(f"Failed to persist file {file_name}: {exc}")
        return "", file_hash, False


@dataclass
class DownloadInput:
    mxc_url: str
    file_type: str
    file_name: str


@dataclass
class DownloadResult:
    file_path: str
    file_size: int
    duration_ms: int
    persistent_path: str = ""
    file_hash: str = ""
    is_duplicate: bool = False


@activity.defn
async def download_file_from_matrix(input: DownloadInput) -> DownloadResult:
    start = time.monotonic()
    activity.logger.info(f"Downloading {input.file_name} from {input.mxc_url}")

    if not input.mxc_url.startswith("mxc://"):
        raise DownloadError(f"Invalid mxc:// URL: {input.mxc_url}")

    parts = input.mxc_url[6:].split("/", 1)
    if len(parts) != 2:
        raise DownloadError(f"Malformed mxc:// URL: {input.mxc_url}")

    server_name, media_id = parts
    download_url = (
        f"{MATRIX_HOMESERVER_URL}/_matrix/client/v1/media/download"
        f"/{server_name}/{media_id}"
    )

    suffix = _EXT_MAP.get(input.file_type, ".bin")
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path = temp.name
    temp.close()

    headers = {}
    if MATRIX_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {MATRIX_ACCESS_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(download_url, headers=headers)
            if response.status_code != 200:
                raise DownloadError(
                    f"HTTP {response.status_code} downloading {input.file_name}: "
                    f"{response.text[:500]}"
                )
            with open(temp_path, "wb") as f:
                f.write(response.content)

        file_size = os.path.getsize(temp_path)

        persistent_path, file_hash, is_duplicate = _persist_file(
            temp_path, input.file_name, input.mxc_url
        )

        elapsed = int((time.monotonic() - start) * 1000)
        dup_tag = " [DUPLICATE]" if is_duplicate else ""
        activity.logger.info(
            f"Downloaded {input.file_name} → {temp_path} ({file_size} bytes, {elapsed}ms), "
            f"hash={file_hash[:12]}..., persisted → {persistent_path}{dup_tag}"
        )
        return DownloadResult(
            file_path=temp_path,
            file_size=file_size,
            duration_ms=elapsed,
            persistent_path=persistent_path,
            file_hash=file_hash,
            is_duplicate=is_duplicate,
        )

    except DownloadError:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    except httpx.TimeoutException as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise DownloadError(f"Timeout downloading {input.file_name}: {e}") from e
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise DownloadError(f"Error downloading {input.file_name}: {e}") from e
