#!/usr/bin/env python3
"""
Password generation utilities for Matrix users.

Provides consistent, secure password generation across the codebase.
"""
import os
import secrets
import string
from typing import Optional


def generate_password(length: int = 16, include_special: bool = False) -> str:
    """
    Generate a secure random password.
    
    Args:
        length: Password length (default 16)
        include_special: Whether to include special characters
        
    Returns:
        Generated password string
    """
    # Development override - use simple password if DEV_MODE is set
    if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
        return "password"
    
    alphabet = string.ascii_letters + string.digits
    if include_special:
        alphabet += "!@#$%^&*"
    
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_agent_password(agent_id: str) -> str:
    """
    Generate a password for an agent user.
    
    Uses a deterministic prefix based on agent_id for easier debugging,
    combined with random characters for security.
    
    Args:
        agent_id: The agent's ID (e.g., "agent-b417b8da-84d2-40dd-97ad-3a35454934f7")
        
    Returns:
        Generated password in format "AgentPass_{short_id}_{random}!"
    """
    if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
        return "password"
    
    # Extract short ID from agent_id
    short_id = agent_id.replace("agent-", "")[:8]
    
    # Generate random suffix
    random_suffix = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    
    return f"AgentPass_{short_id}_{random_suffix}!"


def generate_service_password(service_name: str) -> str:
    """
    Generate a password for a service user (bridge bots, etc).
    
    Args:
        service_name: Name of the service (e.g., "agent_mail_bridge")
        
    Returns:
        Generated password
    """
    if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
        return "password"
    
    # Generate secure random password with special chars for services
    random_part = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    return f"{service_name}_{random_part}!"


# Standard password for agent_mail_bridge (stored in database, not hardcoded)
# When resetting, use generate_service_password("agent_mail_bridge")
