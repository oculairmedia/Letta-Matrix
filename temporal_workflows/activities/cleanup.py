import os
import time
from dataclasses import dataclass

from temporalio import activity

from .common import FileActivityError
from .download import _mutate_hash_index_locked


@dataclass
class CleanupArtifactsInput:
    temp_file_path: str = ""
    persistent_path: str = ""
    file_hash: str = ""
    remove_persistent: bool = False


@dataclass
class CleanupArtifactsResult:
    temp_removed: bool = False
    persistent_removed: bool = False
    hash_entry_removed: bool = False
    duration_ms: int = 0


@activity.defn
async def cleanup_file_artifacts(input: CleanupArtifactsInput) -> CleanupArtifactsResult:
    start = time.monotonic()
    temp_removed = False
    persistent_removed = False
    hash_entry_removed = False

    try:
        if input.temp_file_path and os.path.exists(input.temp_file_path):
            os.unlink(input.temp_file_path)
            temp_removed = True

        if input.remove_persistent:
            if input.persistent_path and os.path.exists(input.persistent_path):
                os.unlink(input.persistent_path)
                persistent_removed = True
                parent = os.path.dirname(input.persistent_path)
                if parent and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)

            if input.file_hash:

                def _remove_hash(index: dict) -> bool:
                    entry = index.get(input.file_hash)
                    if not entry:
                        return False
                    if input.persistent_path and entry.get("persistent_path") != input.persistent_path:
                        return False
                    index.pop(input.file_hash, None)
                    return True

                hash_entry_removed = _mutate_hash_index_locked(_remove_hash)

        elapsed = int((time.monotonic() - start) * 1000)
        activity.logger.info(
            "Cleanup completed: "
            f"temp_removed={temp_removed}, persistent_removed={persistent_removed}, "
            f"hash_entry_removed={hash_entry_removed}, {elapsed}ms"
        )
        return CleanupArtifactsResult(
            temp_removed=temp_removed,
            persistent_removed=persistent_removed,
            hash_entry_removed=hash_entry_removed,
            duration_ms=elapsed,
        )
    except Exception as e:
        raise FileActivityError(f"Failed cleanup artifacts: {e}") from e
