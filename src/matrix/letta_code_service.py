"""
Letta Code (filesystem mode) service — state management, API calls, and /fs-* commands.

Extracted from client.py as a standalone module.
Re-exported by client.py for backward compatibility.

Dependencies:
  - Config, LettaCodeApiError from src.matrix.config
  - send_as_agent() is passed as a callable (not imported from client.py)
    to avoid circular imports during the decomposition.
"""
import json
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp

from src.matrix.config import Config, LettaCodeApiError


# ── State Persistence ────────────────────────────────────────────────

LETTACODE_STATE_PATH = os.getenv(
    "LETTA_CODE_STATE_PATH", "/app/data/letta_code_state.json"
)
_letta_code_state: Dict[str, Dict[str, Any]] = {}


def _load_letta_code_state() -> None:
    global _letta_code_state
    if _letta_code_state:
        return
    try:
        if os.path.exists(LETTACODE_STATE_PATH):
            with open(LETTACODE_STATE_PATH, "r") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    _letta_code_state = data
    except Exception:
        _letta_code_state = {}


def _save_letta_code_state() -> None:
    dir_path = os.path.dirname(LETTACODE_STATE_PATH)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    with open(LETTACODE_STATE_PATH, "w") as fh:
        json.dump(_letta_code_state, fh)


def get_letta_code_room_state(room_id: str) -> Dict[str, Any]:
    _load_letta_code_state()
    return dict(_letta_code_state.get(room_id, {}))


def update_letta_code_room_state(
    room_id: str, updates: Dict[str, Any]
) -> Dict[str, Any]:
    _load_letta_code_state()
    room_state = _letta_code_state.get(room_id, {})
    room_state.update(updates)
    _letta_code_state[room_id] = room_state
    _save_letta_code_state()
    return dict(room_state)


# ── Letta Code API ───────────────────────────────────────────────────

async def call_letta_code_api(
    config: Config,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Make a request to the Letta Code CLI API."""
    base = (config.letta_code_api_url or "").rstrip("/")
    if not base:
        raise LettaCodeApiError(503, "Letta Code API URL not configured")
    url = f"{base}{path}"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.request(method, url, json=payload) as response:
            text = await response.text()
            data: Optional[Any] = None
            if text:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw": text}
            if response.status >= 400:
                message = ""
                if isinstance(data, dict):
                    message = data.get("error") or data.get("message") or ""
                raise LettaCodeApiError(
                    response.status, message or text or "Request failed", data
                )
            if data is None:
                return {}
            return data


# ── Project Resolution ───────────────────────────────────────────────

async def resolve_letta_project_dir(
    room_id: str,
    agent_id: str,
    config: Config,
    logger: logging.Logger,
    override_path: Optional[str] = None,
) -> Optional[str]:
    """Resolve the filesystem project directory for a room/agent pair."""
    if override_path:
        # One-shot override: don't persist to room state (bd-lc4b)
        return override_path
    state = get_letta_code_room_state(room_id)
    project_dir = state.get("projectDir")
    if project_dir:
        return project_dir
    try:
        session_info = await call_letta_code_api(
            config, "GET", f"/api/letta-code/sessions/{agent_id}"
        )
        if session_info:
            project_dir = session_info.get("projectDir")
            if project_dir:
                update_letta_code_room_state(room_id, {"projectDir": project_dir})
                return project_dir
    except LettaCodeApiError as exc:
        if exc.status_code != 404:
            logger.warning(
                "Failed to resolve Letta Code session",
                extra={
                    "room_id": room_id,
                    "agent_id": agent_id,
                    "status_code": exc.status_code,
                    "error": str(exc),
                },
            )
    except Exception as exc:
        logger.debug(f"Letta Code API unreachable for session resolve: {exc}")
    return None


# ── Task Execution ───────────────────────────────────────────────────

# Type alias for the send_as_agent callback to avoid circular imports.
SendFn = Callable[..., Awaitable[bool]]


async def run_letta_code_task(
    *,
    room_id: str,
    agent_id: str,
    agent_name: str,
    project_dir: Optional[str],
    prompt: str,
    config: Config,
    logger: logging.Logger,
    send_fn: SendFn,
    wrap_response: bool = True,
) -> bool:
    """Execute a filesystem task via the Letta Code CLI API.

    *send_fn* must have the signature ``send_fn(room_id, message, config, logger)``.
    It is used to send results back to the Matrix room.
    """
    if not project_dir:
        await send_fn(room_id, "No filesystem session found. Run /fs-link first.", config, logger)
        return False
    payload = {
        "agentId": agent_id,
        "prompt": prompt,
        "projectDir": project_dir,
    }
    try:
        result = await call_letta_code_api(
            config, "POST", "/api/letta-code/task", payload, timeout=900.0
        )
        output = result.get("result") or result.get("message") or ""
        if not output:
            output = "Task completed with no output."
        if len(output) > 4000:
            output = output[:4000] + "…"
        success = result.get("success", False)
        if wrap_response:
            status_line = "Task succeeded" if success else "Task failed"
            response_text = (
                f"[Filesystem Task]\n{status_line}\n"
                f"Agent: {agent_name}\nPath: {project_dir}\n\n{output}"
            )
            if not success:
                error_text = result.get("error") or ""
                if error_text:
                    response_text += f"\nError: {error_text}"
        else:
            if success:
                response_text = output
            else:
                error_text = result.get("error") or ""
                response_text = f"[Filesystem Error]\n{error_text or output}"
        await send_fn(room_id, response_text, config, logger)
        return success
    except LettaCodeApiError as exc:
        detail = ""
        if isinstance(exc.details, dict):
            detail = exc.details.get("error") or exc.details.get("message") or ""
        message = f"Filesystem task failed ({exc.status_code}): {detail or str(exc)}"
        await send_fn(room_id, message, config, logger)
        return False


# ── /fs-* Command Handler ───────────────────────────────────────────

async def handle_letta_code_command(
    room,
    event,
    config: Config,
    logger: logging.Logger,
    send_fn: SendFn,
    agent_mapping: Optional[Dict[str, Any]] = None,
    agent_id_hint: Optional[str] = None,
    agent_name_hint: Optional[str] = None,
) -> bool:
    """Handle /fs-link, /fs-run, /fs-task commands. Returns True if handled."""
    if not config.letta_code_enabled:
        return False
    body = getattr(event, "body", None)
    if not body:
        return False
    trimmed = body.strip()
    lowered = trimmed.lower()
    if not lowered.startswith("/fs-"):
        return False

    from src.models.agent_mapping import AgentMappingDB

    agent_id = agent_id_hint
    agent_name = agent_name_hint
    if agent_mapping and not agent_name:
        agent_name = agent_mapping.get("agent_name") or agent_mapping.get("agentName")
    if not agent_id or not agent_name:
        db = AgentMappingDB()
        mapping = db.get_by_room_id(room.room_id)
        if not mapping:
            await send_fn(room.room_id, "No agent mapping for this room.", config, logger)
            return True
        agent_id = str(mapping.agent_id)
        agent_name = str(mapping.agent_name)

    state = get_letta_code_room_state(room.room_id)
    parts = trimmed.split(" ", 1)
    command = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    # ── /fs-link ──────────────────────────────────────────────────
    if command == "/fs-link":
        project_dir = args if args else None

        # Auto-detect path from VibSync if no path provided
        if not project_dir and agent_name:
            try:
                projects_response = await call_letta_code_api(config, "GET", "/api/projects")
                projects = projects_response.get("projects", [])
                search_name = agent_name
                if search_name.startswith("Huly - "):
                    search_name = search_name[7:]
                for proj in projects:
                    if proj.get("name", "").lower() == search_name.lower():
                        project_dir = proj.get("filesystem_path")
                        if project_dir:
                            logger.info(
                                f"Auto-detected filesystem path for {agent_name}: {project_dir}"
                            )
                        break
            except Exception as e:
                logger.warning(f"Failed to auto-detect filesystem path: {e}")

        if not project_dir:
            await send_fn(
                room.room_id,
                "Usage: /fs-link /path/to/project\n(Could not auto-detect path for this agent)",
                config,
                logger,
            )
            return True

        payload = {
            "agentId": agent_id,
            "projectDir": project_dir,
            "agentName": agent_name,
        }
        try:
            response = await call_letta_code_api(config, "POST", "/api/letta-code/link", payload)
            message = response.get("message") or f"Agent {agent_id} linked to {project_dir}"
            update_letta_code_room_state(room.room_id, {"projectDir": project_dir})
            await send_fn(room.room_id, message, config, logger)
        except LettaCodeApiError as exc:
            detail = ""
            if isinstance(exc.details, dict):
                detail = exc.details.get("error") or exc.details.get("message") or ""
            await send_fn(
                room.room_id,
                f"Link failed ({exc.status_code}): {detail or str(exc)}",
                config,
                logger,
            )
        return True

    # ── /fs-run ───────────────────────────────────────────────────
    if command == "/fs-run":
        if not args:
            await send_fn(
                room.room_id,
                "Usage: /fs-run [--path=/opt/project] prompt",
                config,
                logger,
            )
            return True
        prompt_text = args
        path_override = None
        if prompt_text.startswith("--path="):
            first_space = prompt_text.find(" ")
            if first_space == -1:
                path_override = prompt_text[len("--path=") :].strip()
                prompt_text = ""
            else:
                path_override = prompt_text[len("--path=") : first_space].strip()
                prompt_text = prompt_text[first_space + 1 :].strip()
        if not prompt_text:
            await send_fn(
                room.room_id,
                "Provide a prompt after the path option.",
                config,
                logger,
            )
            return True
        project_dir = await resolve_letta_project_dir(
            room.room_id, agent_id, config, logger, override_path=path_override
        )
        if not project_dir:
            await send_fn(
                room.room_id,
                "No filesystem session found. Run /fs-link first.",
                config,
                logger,
            )
            return True

        fs_run_prompt = prompt_text
        if event.sender.startswith("@oc_"):
            from src.matrix import formatter as matrix_formatter

            fs_run_prompt = matrix_formatter.wrap_opencode_routing(
                prompt_text, event.sender
            )
            logger.info("[OPENCODE-FS-RUN] Injected @mention instruction for /fs-run command")

        await run_letta_code_task(
            room_id=room.room_id,
            agent_id=agent_id,
            agent_name=agent_name,
            project_dir=project_dir,
            prompt=fs_run_prompt,
            config=config,
            logger=logger,
            send_fn=send_fn,
            wrap_response=True,
        )
        return True

    # ── /fs-task ──────────────────────────────────────────────────
    if command == "/fs-task":
        normalized = args.lower()
        state_enabled = bool(state.get("enabled"))
        if not args:
            desired = not state_enabled
        elif normalized in ("on", "enable", "start"):
            desired = True
        elif normalized in ("off", "disable", "stop"):
            desired = False
        elif normalized in ("status", "state"):
            status = "ENABLED" if state_enabled else "DISABLED"
            info = state.get("projectDir") or "not set"
            environment = "Letta Code" if state_enabled else "Cloud-only"
            await send_fn(
                room.room_id,
                f"Filesystem mode is {status}\nEnvironment: {environment}\nProject path: {info}",
                config,
                logger,
            )
            return True
        else:
            await send_fn(
                room.room_id, "Usage: /fs-task [on|off|status]", config, logger
            )
            return True

        if desired:
            project_dir = state.get("projectDir")
            if not project_dir:
                project_dir = await resolve_letta_project_dir(
                    room.room_id, agent_id, config, logger
                )
            if not project_dir:
                await send_fn(
                    room.room_id,
                    "Link a project with /fs-link before enabling filesystem mode.",
                    config,
                    logger,
                )
                return True
            update_letta_code_room_state(
                room.room_id, {"enabled": True, "projectDir": project_dir}
            )
            await send_fn(
                room.room_id,
                f"Filesystem mode ENABLED\nEnvironment: Letta Code (path: {project_dir})\n"
                f"All new prompts will run inside the project workspace.",
                config,
                logger,
            )
        else:
            update_letta_code_room_state(room.room_id, {"enabled": False})
            await send_fn(
                room.room_id,
                "Filesystem mode DISABLED\nEnvironment: Cloud-only (standard Letta API).",
                config,
                logger,
            )
        return True

    return False
