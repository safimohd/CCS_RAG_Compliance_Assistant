"""
user_flow_healthcare.py  --  Patient-facing chat flow for Karnataka Healthcare Assistant.
Renders the user-facing guided chat experience.
Import and call render_user_flow() from app_healthcare.py for the 'user' role.

This is the healthcare equivalent of user_flow.py (RERA property pipeline).
No RERA, property, or market-rates code lives here.
"""

from structured_chat_healthcare import render_user_chat


def set_user_rag_store(store) -> None:
    """Pass the already-loaded document index to the patient chat."""
    render_user_chat.__globals__["_USER_RAG_STORE"] = store

def render_user_flow() -> None:
    """Entry point: render the healthcare user chat. Call from app_healthcare.py."""
    render_user_chat()
