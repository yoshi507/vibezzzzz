"""
Groq AI chat for Vibezzzzz — conversation memory + personality.
"""
from __future__ import annotations

import os
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional

import aiosqlite
import httpx

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_PERSONALITY = (
    "You are Vibezzzzz, a chill Discord bot with pure vibes. "
    "You're laid-back, friendly, a little playful, and you keep things positive. "
    "Talk casually, use light slang when it fits, and keep replies short (1-3 sentences) "
    "unless the user asks for more. You're helpful but never stiff or corporate."
)

# Per-user memory: last N message pairs (user + assistant)
MAX_HISTORY = 12
_memory: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY * 2))

# Cached personality (reloaded from DB)
_personality_cache: Optional[str] = None


async def ensure_ai_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        await db.commit()
        async with db.execute(
            "SELECT value FROM bot_settings WHERE key = 'ai_personality'"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO bot_settings (key, value) VALUES ('ai_personality', ?)",
                    (DEFAULT_PERSONALITY,),
                )
                await db.commit()


async def get_personality(db_path: str) -> str:
    global _personality_cache
    if _personality_cache is not None:
        return _personality_cache
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key = 'ai_personality'"
            ) as cur:
                row = await cur.fetchone()
                if row and row[0]:
                    _personality_cache = row[0]
                    return _personality_cache
    except Exception:
        pass
    return DEFAULT_PERSONALITY


async def set_personality(db_path: str, text: str) -> None:
    global _personality_cache
    text = (text or "").strip()[:4000] or DEFAULT_PERSONALITY
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO bot_settings (key, value) VALUES ('ai_personality', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (text,),
        )
        await db.commit()
    _personality_cache = text


def clear_memory(user_id: int) -> None:
    _memory.pop(user_id, None)


def clear_all_memory() -> None:
    _memory.clear()


async def chat(db_path: str, user_id: int, user_message: str) -> str:
    """Send a message to Groq with personality + per-user history."""
    if not GROQ_API_KEY:
        return "⚠️ AI is offline — set `GROQ_API_KEY` on the server."

    personality = await get_personality(db_path)
    history = _memory[user_id]

    messages: List[dict] = [{"role": "system", "content": personality}]
    messages.extend(list(history))
    messages.append({"role": "user", "content": user_message[:2000]})

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.8,
                    "max_tokens": 300,
                },
            )
            if res.status_code != 200:
                detail = res.text[:200]
                return f"⚠️ Groq error ({res.status_code}): {detail}"
            data = res.json()
            reply = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ AI request failed: {type(e).__name__}: {e}"

    history.append({"role": "user", "content": user_message[:2000]})
    history.append({"role": "assistant", "content": reply})
    return reply


def should_auto_reply(content: str) -> bool:
    """True if message mentions chill / vibezzzzz triggers."""
    if not content:
        return False
    lower = content.lower()
    return (
        "chill vibezzzzz" in lower
        or "vibezzzzz" in lower
        or "chill" in lower
    )
