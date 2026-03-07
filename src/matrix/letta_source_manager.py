import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.core.retry import retry_async


class FileUploadError(Exception):
    pass


if TYPE_CHECKING:
    from src.matrix.file_download import FileMetadata

try:
    from src.matrix.file_download import FileUploadError, FileMetadata  # type: ignore[assignment,no-redef]
except Exception:
    pass


_executor = ThreadPoolExecutor(max_workers=4)


class LettaSourceManager:
    def __init__(self, letta_client, config_defaults: dict, logger):
        self.letta_client = letta_client
        self.logger = logger

        self.embedding_model = config_defaults["embedding_model"]
        self.embedding_endpoint = config_defaults["embedding_endpoint"]
        self.embedding_endpoint_type = config_defaults["embedding_endpoint_type"]
        self.embedding_dim = config_defaults["embedding_dim"]
        self.embedding_chunk_size = config_defaults["embedding_chunk_size"]

        self.max_retries = config_defaults.get("max_retries", 3)
        self.retry_delay = config_defaults.get("retry_delay", 1.0)

        self._source_cache: Dict[str, str] = {}
        self._cache_lock = asyncio.Lock()
        self._room_locks: Dict[str, asyncio.Lock] = {}

    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            functools.partial(func, *args, **kwargs),
        )

    def get_embedding_config(self, agent_id: Optional[str] = None) -> dict:
        if agent_id:
            try:
                agent = self.letta_client.agents.retrieve(agent_id)
                if agent and agent.embedding_config:
                    ec = agent.embedding_config
                    config = {
                        "embedding_model": ec.embedding_model,
                        "embedding_endpoint_type": ec.embedding_endpoint_type or "openai",
                        "embedding_dim": ec.embedding_dim,
                        "embedding_chunk_size": ec.embedding_chunk_size or 300,
                    }
                    if ec.embedding_endpoint:
                        config["embedding_endpoint"] = ec.embedding_endpoint
                    self.logger.info(
                        f"Using agent's embedding config: model={config['embedding_model']}, dim={config['embedding_dim']}"
                    )
                    return config
            except Exception as e:
                self.logger.warning(f"Failed to fetch agent embedding config: {e}")

        config = {
            "embedding_model": self.embedding_model,
            "embedding_endpoint_type": self.embedding_endpoint_type,
            "embedding_dim": self.embedding_dim,
            "embedding_chunk_size": self.embedding_chunk_size,
        }
        if self.embedding_endpoint:
            config["embedding_endpoint"] = self.embedding_endpoint
        self.logger.info(f"Using fallback embedding config: model={self.embedding_model}, dim={self.embedding_dim}")
        return config

    async def get_or_create_source(self, room_id: str, agent_id: Optional[str] = None) -> str:
        # Per-room lock prevents duplicate folder creation for the same room
        async with self._cache_lock:
            if room_id not in self._room_locks:
                self._room_locks[room_id] = asyncio.Lock()
            room_lock = self._room_locks[room_id]

        async with room_lock:
            # Re-check cache under room lock
            if room_id in self._source_cache:
                return self._source_cache[room_id]

            safe_room_id = room_id.replace("!", "").replace(":", "-")
            folder_name = f"matrix-{safe_room_id}"

            async def _do_get_or_create() -> str:
                try:
                    folders_page = await self._run_sync(
                        self.letta_client.folders.list,
                        name=folder_name,
                    )
                    folders = folders_page.items if hasattr(folders_page, "items") else folders_page
                    if folders and len(folders) > 0:
                        folder_id = folders[0].id
                        self.logger.info(f"Found existing folder by name: {folder_id}")
                        return folder_id
                except Exception as e:
                    self.logger.debug(f"Folder not found by name: {e}")

                self.logger.info(f"Creating new folder: {folder_name}")
                embedding_config = await self._run_sync(self.get_embedding_config, agent_id)

                try:
                    folder = await self._run_sync(
                        self.letta_client.folders.create,
                        name=folder_name,
                        description=f"Documents from Matrix room {room_id}",
                    )
                    self.logger.info(f"Created new folder {folder_name}: {folder.id}")
                    return folder.id
                except Exception as e:
                    error_str = str(e)
                    if "409" in error_str or "already exists" in error_str.lower() or "unique" in error_str.lower():
                        self.logger.info(f"Folder {folder_name} already exists (conflict), fetching...")
                        try:
                            folders_page = await self._run_sync(
                                self.letta_client.folders.list,
                                name=folder_name,
                            )
                            folders = folders_page.items if hasattr(folders_page, "items") else folders_page
                            if folders and len(folders) > 0:
                                folder_id = folders[0].id
                                self.logger.info(f"Found folder after conflict: {folder_id}")
                                return folder_id
                        except Exception as e2:
                            self.logger.error(f"Failed to get folder after 409: {e2}")
                    raise FileUploadError(f"Failed to create folder: {e}")

            folder_id = await retry_async(
                _do_get_or_create,
                operation_name="Get/create Letta folder",
                max_attempts=self.max_retries,
                base_delay=self.retry_delay,
                logger=self.logger,
            )

            self._source_cache[room_id] = folder_id

            return folder_id

    async def attach_source_to_agent(self, source_id: str, agent_id: str) -> None:
        try:
            attached_page = await self._run_sync(
                self.letta_client.agents.folders.list,
                agent_id,
            )
            attached_folders = attached_page.items if hasattr(attached_page, "items") else attached_page

            for folder in attached_folders:
                if folder.id == source_id:
                    self.logger.info(f"Folder {source_id} already attached to agent {agent_id}")
                    return

            await self._run_sync(
                lambda: self.letta_client.agents.folders.attach(source_id, agent_id=agent_id)
            )
            self.logger.info(f"Attached folder {source_id} to agent {agent_id}")

        except Exception as e:
            self.logger.warning(f"Failed to attach folder to agent: {e}")

    async def upload_to_letta(self, file_path: str, source_id: str, metadata: Any) -> str:
        async def _do_upload() -> str:
            with open(file_path, "rb") as f:
                file_content = f.read()

            result = await self._run_sync(
                self.letta_client.folders.files.upload,
                source_id,
                file=(metadata.file_name, file_content, metadata.file_type),
            )

            file_id = result.id if hasattr(result, "id") else str(result)

            self.logger.info(f"File uploaded to Letta, file ID: {file_id}")
            return file_id

        return await retry_async(
            _do_upload,
            operation_name="Letta file upload",
            max_attempts=self.max_retries,
            base_delay=self.retry_delay,
            logger=self.logger,
        )

    async def poll_file_status(self, source_id: str, file_id: str, timeout: int = 300, interval: int = 2) -> bool:
        if file_id == "sync-complete":
            return True

        elapsed = 0
        consecutive_errors = 0
        max_consecutive_errors = 3

        while elapsed < timeout:
            try:
                files_page = await self._run_sync(
                    self.letta_client.folders.files.list,
                    source_id,
                )
                files = files_page.items if hasattr(files_page, "items") else files_page

                file_data = None
                for f in files:
                    if f.id == file_id:
                        file_data = f
                        break

                if not file_data:
                    self.logger.warning(f"File {file_id} not found in folder {source_id}")
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        return False
                    await asyncio.sleep(interval)
                    elapsed += interval
                    continue

                consecutive_errors = 0
                status = (file_data.processing_status or "").lower()

                self.logger.debug(f"File {file_id} processing_status: {status}")

                if status in ["completed", "success", "done", "embedded"]:
                    self.logger.info(f"File {file_id} processed successfully")
                    return True
                if status in ["error", "failed"]:
                    error_msg = getattr(file_data, "error_message", "Unknown error")
                    self.logger.error(f"File {file_id} processing failed: {error_msg}")
                    return False

                await asyncio.sleep(interval)
                elapsed += interval

            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.error(f"Error polling file status after {max_consecutive_errors} attempts: {e}")
                    return False
                self.logger.warning(f"Error polling file status: {e}")
                await asyncio.sleep(interval)
                elapsed += interval

        self.logger.warning(f"File {file_id} polling timed out after {timeout}s")
        return False

    async def get_or_create_folder(self, room_id: str, agent_id: Optional[str] = None) -> str:
        return await self.get_or_create_source(room_id, agent_id)

    async def ensure_search_tool_attached(self, agent_id: str) -> None:
        try:
            tools_page = await self._run_sync(
                self.letta_client.tools.list,
                name="search_documents",
            )
            tools_list = list(tools_page)
            if not tools_list:
                self.logger.warning("search_documents tool not found in Letta — cannot auto-attach")
                return

            search_tool = tools_list[0]
            search_tool_id = search_tool.id

            agent_tools_page = await self._run_sync(
                self.letta_client.agents.tools.list,
                agent_id,
            )
            agent_tools = list(agent_tools_page)
            for t in agent_tools:
                if t.id == search_tool_id:
                    self.logger.debug(f"search_documents already attached to agent {agent_id}")
                    return

            await self._run_sync(
                self.letta_client.agents.tools.attach,
                search_tool_id,
                agent_id=agent_id,
            )
            self.logger.info(f"Auto-attached search_documents tool to agent {agent_id}")

        except Exception as e:
            self.logger.warning(f"Failed to auto-attach search_documents to agent {agent_id}: {e}")
