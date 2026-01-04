"""
Matrix Poll Handler - Intercepts /poll commands from Letta agents.

Command syntax:
    /poll "Question?" "Option 1" "Option 2" "Option 3"
    /poll disclosed "Question?" "Yes" "No"
    /poll undisclosed "Question?" "A" "B"
    /poll-results $poll_event_id
    /poll-close $poll_event_id
"""

import re
import uuid
import logging
import aiohttp
import time
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

POLL_START_TYPE = "org.matrix.msc3381.poll.start"
POLL_RESPONSE_TYPE = "org.matrix.msc3381.poll.response"
POLL_END_TYPE = "org.matrix.msc3381.poll.end"
POLL_KIND_DISCLOSED = "org.matrix.msc3381.poll.disclosed"
POLL_KIND_UNDISCLOSED = "org.matrix.msc3381.poll.undisclosed"


@dataclass
class ParsedPoll:
    question: str
    options: List[str]
    kind: str = POLL_KIND_DISCLOSED
    max_selections: int = 1


def parse_poll_command(text: str) -> Optional[ParsedPoll]:
    """Parse /poll command. Returns None if invalid (need question + 2+ options)."""
    text = text.strip()
    
    if not text.startswith('/poll'):
        return None
    
    remainder = text[5:].strip()
    
    kind = POLL_KIND_DISCLOSED
    if remainder.startswith('disclosed '):
        kind = POLL_KIND_DISCLOSED
        remainder = remainder[10:].strip()
    elif remainder.startswith('undisclosed '):
        kind = POLL_KIND_UNDISCLOSED
        remainder = remainder[12:].strip()
    
    pattern = r'"([^"\\]*(?:\\.[^"\\]*)*)"'
    matches = re.findall(pattern, remainder)
    
    if len(matches) < 3:
        logger.warning(f"[POLL] Invalid poll command - need question + at least 2 options: {text[:100]}")
        return None
    
    question = matches[0]
    options = matches[1:21]
    
    return ParsedPoll(question=question, options=options, kind=kind, max_selections=1)


def is_poll_command(text: str) -> bool:
    return text.strip().startswith('/poll ')


def build_poll_start_event(poll: ParsedPoll) -> Dict[str, Any]:
    fallback_lines = [poll.question]
    for i, opt in enumerate(poll.options, 1):
        fallback_lines.append(f"{i}. {opt}")
    
    answers = [
        {"id": f"opt_{i}", "org.matrix.msc1767.text": opt}
        for i, opt in enumerate(poll.options)
    ]
    
    return {
        "org.matrix.msc1767.text": "\n".join(fallback_lines),
        "org.matrix.msc3381.poll.start": {
            "kind": poll.kind,
            "max_selections": poll.max_selections,
            "question": {"org.matrix.msc1767.text": poll.question},
            "answers": answers
        }
    }


async def send_poll_as_agent(
    room_id: str,
    poll: ParsedPoll,
    config: Any,
    logger_instance: logging.Logger,
    reply_to_event_id: Optional[str] = None,
    agent_id: Optional[str] = None
) -> Optional[str]:
    try:
        from src.core.mapping_service import get_mapping_by_room_id
        agent_mapping = get_mapping_by_room_id(room_id)
        
        if not agent_mapping:
            logger_instance.warning(f"[POLL] No agent mapping found for room {room_id}")
            return None
        
        agent_name = agent_mapping.get("agent_name", "Unknown")
        resolved_agent_id = agent_id or agent_mapping.get("agent_id")
        logger_instance.info(f"[POLL] Sending poll as {agent_name}: {poll.question}")
        
        agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
        agent_password = agent_mapping["matrix_password"]
        
        async with aiohttp.ClientSession() as session:
            login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
            login_data = {"type": "m.login.password", "user": agent_username, "password": agent_password}
            
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger_instance.error(f"[POLL] Login failed for {agent_username}: {response.status} - {error_text}")
                    return None
                
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                if not agent_token:
                    logger_instance.error(f"[POLL] No token received for {agent_username}")
                    return None
            
            poll_content = build_poll_start_event(poll)
            if reply_to_event_id:
                poll_content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}
            
            txn_id = str(uuid.uuid4())
            event_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/{POLL_START_TYPE}/{txn_id}"
            headers = {"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"}
            
            async with session.put(event_url, headers=headers, json=poll_content) as response:
                if response.status == 200:
                    result = await response.json()
                    event_id = result.get("event_id")
                    logger_instance.info(f"[POLL] Created poll {event_id}")
                    track_poll(event_id, room_id, poll, resolved_agent_id)
                    return event_id
                else:
                    response_text = await response.text()
                    logger_instance.error(f"[POLL] Failed to send poll: {response.status} - {response_text}")
                    return None
                    
    except Exception as e:
        logger_instance.error(f"[POLL] Exception: {e}", exc_info=True)
        return None


async def get_agent_token(room_id: str, config: Any, logger_instance: logging.Logger) -> Optional[str]:
    from src.core.mapping_service import get_mapping_by_room_id
    agent_mapping = get_mapping_by_room_id(room_id)
    if not agent_mapping:
        return None
    
    agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
    agent_password = agent_mapping["matrix_password"]
    
    async with aiohttp.ClientSession() as session:
        login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
        login_data = {"type": "m.login.password", "user": agent_username, "password": agent_password}
        
        async with session.post(login_url, json=login_data) as response:
            if response.status != 200:
                return None
            auth_data = await response.json()
            return auth_data.get("access_token")


async def handle_poll_results_command(
    room_id: str,
    poll_event_id: str,
    config: Any,
    logger_instance: logging.Logger
) -> str:
    agent_token = await get_agent_token(room_id, config, logger_instance)
    if not agent_token:
        return f"❌ Could not authenticate to get poll results"
    
    results = await query_poll_results(room_id, poll_event_id, config, logger_instance, agent_token)
    if not results:
        return f"❌ Could not retrieve results for poll {poll_event_id}"
    
    return format_poll_results(results)


async def handle_poll_close_command(
    room_id: str,
    poll_event_id: str,
    config: Any,
    logger_instance: logging.Logger
) -> str:
    agent_token = await get_agent_token(room_id, config, logger_instance)
    if not agent_token:
        return f"❌ Could not authenticate to close poll"
    
    results = await query_poll_results(room_id, poll_event_id, config, logger_instance, agent_token)
    if not results:
        return f"❌ Could not retrieve results for poll {poll_event_id}"
    
    end_event_id = await send_poll_end_event(room_id, poll_event_id, results, config, logger_instance, agent_token)
    if not end_event_id:
        return f"❌ Failed to close poll {poll_event_id}"
    
    return f"✅ Poll closed!\n\n{format_poll_results(results)}"


async def process_agent_response(
    room_id: str,
    response_text: str,
    config: Any,
    logger_instance: logging.Logger,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    text = response_text.strip()
    lines = text.split('\n')
    first_line = lines[0]
    remaining_lines = lines[1:] if len(lines) > 1 else []
    
    if is_poll_command(first_line):
        poll = parse_poll_command(first_line)
        if poll:
            event_id = await send_poll_as_agent(
                room_id=room_id,
                poll=poll,
                config=config,
                logger_instance=logger_instance,
                reply_to_event_id=reply_to_event_id
            )
            remaining = '\n'.join(remaining_lines).strip() if remaining_lines else None
            return (True, remaining, event_id)
        logger_instance.warning(f"[POLL] Failed to parse poll command, sending as text")
        return (False, response_text, None)
    
    if is_poll_results_command(first_line):
        poll_event_id = parse_poll_results_command(first_line)
        if poll_event_id:
            result_text = await handle_poll_results_command(
                room_id=room_id,
                poll_event_id=poll_event_id,
                config=config,
                logger_instance=logger_instance
            )
            remaining = '\n'.join(remaining_lines).strip() if remaining_lines else None
            combined = result_text
            if remaining:
                combined = f"{result_text}\n\n{remaining}"
            return (True, combined, None)
        return (False, response_text, None)
    
    if is_poll_close_command(first_line):
        poll_event_id = parse_poll_close_command(first_line)
        if poll_event_id:
            result_text = await handle_poll_close_command(
                room_id=room_id,
                poll_event_id=poll_event_id,
                config=config,
                logger_instance=logger_instance
            )
            remaining = '\n'.join(remaining_lines).strip() if remaining_lines else None
            combined = result_text
            if remaining:
                combined = f"{result_text}\n\n{remaining}"
            return (True, combined, None)
        return (False, response_text, None)
    
    return (False, response_text, None)


@dataclass
class ActivePoll:
    event_id: str
    room_id: str
    question: str
    options: List[str]
    option_ids: List[str]
    created_at: float
    agent_id: Optional[str] = None


_active_polls: Dict[str, ActivePoll] = {}


def track_poll(event_id: str, room_id: str, poll: ParsedPoll, agent_id: Optional[str] = None) -> None:
    option_ids = [f"opt_{i}" for i in range(len(poll.options))]
    _active_polls[event_id] = ActivePoll(
        event_id=event_id,
        room_id=room_id,
        question=poll.question,
        options=poll.options,
        option_ids=option_ids,
        created_at=time.time(),
        agent_id=agent_id
    )
    logger.info(f"[POLL] Tracking poll {event_id}: {poll.question}")


def get_active_poll(event_id: str) -> Optional[ActivePoll]:
    return _active_polls.get(event_id)


def is_poll_results_command(text: str) -> bool:
    return text.strip().startswith('/poll-results ')


def is_poll_close_command(text: str) -> bool:
    return text.strip().startswith('/poll-close ')


def parse_poll_results_command(text: str) -> Optional[str]:
    text = text.strip()
    if not text.startswith('/poll-results '):
        return None
    event_id = text[14:].strip()
    if event_id.startswith('$'):
        return event_id
    return None


def parse_poll_close_command(text: str) -> Optional[str]:
    text = text.strip()
    if not text.startswith('/poll-close '):
        return None
    event_id = text[12:].strip()
    if event_id.startswith('$'):
        return event_id
    return None


@dataclass
class PollResults:
    poll_event_id: str
    question: str
    total_votes: int
    results: Dict[str, int]
    option_labels: Dict[str, str]
    voters: Dict[str, List[str]]


async def query_poll_results(
    room_id: str,
    poll_event_id: str,
    config: Any,
    logger_instance: logging.Logger,
    agent_token: str
) -> Optional[PollResults]:
    active_poll = get_active_poll(poll_event_id)
    if not active_poll:
        logger_instance.warning(f"[POLL] Poll {poll_event_id} not found in tracker")
        return None
    
    try:
        async with aiohttp.ClientSession() as session:
            relations_url = f"{config.homeserver_url}/_matrix/client/v1/rooms/{room_id}/relations/{poll_event_id}/{POLL_RESPONSE_TYPE}"
            headers = {"Authorization": f"Bearer {agent_token}"}
            
            all_responses: List[Dict[str, Any]] = []
            next_batch: Optional[str] = None
            
            while True:
                url = relations_url
                if next_batch:
                    url += f"?from={next_batch}"
                
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger_instance.warning(f"[POLL] Failed to get relations: {response.status} - {error_text}")
                        break
                    
                    data = await response.json()
                    chunk = data.get("chunk", [])
                    all_responses.extend(chunk)
                    
                    next_batch = data.get("next_batch")
                    if not next_batch:
                        break
            
            votes: Dict[str, str] = {}
            for event in all_responses:
                sender = event.get("sender", "")
                content = event.get("content", {})
                poll_response = content.get("org.matrix.msc3381.poll.response", {})
                answers = poll_response.get("answers", [])
                
                if answers:
                    votes[sender] = answers[0]
            
            results: Dict[str, int] = {opt_id: 0 for opt_id in active_poll.option_ids}
            voters: Dict[str, List[str]] = {opt_id: [] for opt_id in active_poll.option_ids}
            
            for sender, vote in votes.items():
                if vote in results:
                    results[vote] += 1
                    voters[vote].append(sender)
            
            option_labels = {
                active_poll.option_ids[i]: active_poll.options[i]
                for i in range(len(active_poll.options))
            }
            
            return PollResults(
                poll_event_id=poll_event_id,
                question=active_poll.question,
                total_votes=len(votes),
                results=results,
                option_labels=option_labels,
                voters=voters
            )
            
    except Exception as e:
        logger_instance.error(f"[POLL] Error querying results: {e}", exc_info=True)
        return None


def format_poll_results(results: PollResults, include_voters: bool = False) -> str:
    lines = [f"📊 Poll Results: {results.question}", f"Total votes: {results.total_votes}", ""]
    
    sorted_options = sorted(
        results.results.items(),
        key=lambda x: x[1],
        reverse=True
    )
    
    for opt_id, count in sorted_options:
        label = results.option_labels.get(opt_id, opt_id)
        pct = (count / results.total_votes * 100) if results.total_votes > 0 else 0
        bar_len = int(pct / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"{label}: {bar} {count} ({pct:.1f}%)")
        
        if include_voters and results.voters.get(opt_id):
            voter_list = ", ".join(results.voters[opt_id][:5])
            if len(results.voters[opt_id]) > 5:
                voter_list += f" +{len(results.voters[opt_id]) - 5} more"
            lines.append(f"  └ {voter_list}")
    
    return "\n".join(lines)


async def send_poll_end_event(
    room_id: str,
    poll_event_id: str,
    results: PollResults,
    config: Any,
    logger_instance: logging.Logger,
    agent_token: str
) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            winner_id = max(results.results.items(), key=lambda x: x[1])[0]
            winner_label = results.option_labels.get(winner_id, winner_id)
            
            end_content = {
                "m.relates_to": {
                    "rel_type": "m.reference",
                    "event_id": poll_event_id
                },
                "org.matrix.msc1767.text": f"The poll has ended. Top answer: {winner_label}",
                "org.matrix.msc3381.poll.end": {}
            }
            
            txn_id = str(uuid.uuid4())
            event_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/{POLL_END_TYPE}/{txn_id}"
            headers = {"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"}
            
            async with session.put(event_url, headers=headers, json=end_content) as response:
                if response.status == 200:
                    result = await response.json()
                    event_id = result.get("event_id")
                    logger_instance.info(f"[POLL] Closed poll {poll_event_id}")
                    
                    if poll_event_id in _active_polls:
                        del _active_polls[poll_event_id]
                    
                    return event_id
                else:
                    response_text = await response.text()
                    logger_instance.error(f"[POLL] Failed to close poll: {response.status} - {response_text}")
                    return None
                    
    except Exception as e:
        logger_instance.error(f"[POLL] Error closing poll: {e}", exc_info=True)
        return None


async def handle_poll_vote(
    room_id: str,
    sender: str,
    poll_event_id: str,
    selected_option_ids: List[str],
    config: Any,
    logger_instance: logging.Logger
) -> Optional[str]:
    poll_data = _active_polls.get(poll_event_id)
    if not poll_data:
        logger_instance.debug(f"[POLL] Vote received for unknown poll {poll_event_id}")
        return None
    
    id_to_text = dict(zip(poll_data.option_ids, poll_data.options))
    selected_options = [id_to_text.get(opt_id, opt_id) for opt_id in selected_option_ids]
    
    option_text = ", ".join(selected_options) if selected_options else "no selection"
    message = f"[POLL VOTE] {sender} voted for: {option_text}\n(Poll: {poll_data.question})"
    
    logger_instance.info(f"[POLL] Vote from {sender} on poll '{poll_data.question}': {option_text}")
    
    return message
