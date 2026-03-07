"""Shared authentication utilities for the Matrix API."""

import os

from fastapi import Header, HTTPException

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "matrix-identity-internal-key")


def verify_internal_key(x_internal_key: str = Header(...)):
    """Verify the internal API key from the request header.

    Raises HTTPException 403 if the key is missing or invalid.
    """
    if x_internal_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid internal API key")
