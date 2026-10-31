"""
Groq AI chat for Vibezzzzz — conversation memory + personality.
"""
from __future__ import annotations

import os
from collections import defaultdict, deque
from typing import Deque, Dict, List

import aiosqlite
import httpx

try:
    from logging_setup import get_logger, timed, log_exception
    log = get_logger("vibezzzzz.ai")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.ai")
    def timed(*a, **k):
        from contextlib import contextmanager
        @contextmanager
        def _n():
            yield
        return _n()
    def log_exception(logger, msg, exc, **kw):
        logger.exception("%s: %s", msg, exc)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_PERSONALITY = (
    "You are Vibezzzzz, a chill Discord bot. Keep replies short, friendly, and a bit playful. "
    "Use light emoji when it fits. Never claim to be human."
)

_memory: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=12))
_personality_cache: str | None = None


async def ensure_ai_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY, value TEXT
            )"""
        )
        await db.commit()


async def get_personality(db_path: str) -> str:
    global _personality_cache
    if _personality_cache:
        return _personality_cache
    await ensure_ai_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM bot_settings WHERE key = 'ai_personality'"
        ) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                _personality_cache = row[0]
                return _personality_cache
    _personality_cache = DEFAULT_PERSONALITY
    return _personality_cache


async def set_personality(db_path: str, text: str) -> None:
    global _personality_cache
    await ensure_ai_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO bot_settings (key, value) VALUES ('ai_personality', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (text,),
        )
        await db.commit()
    _personality_cache = text
    log.info("AI personality updated (%d chars)", len(text))


def should_auto_reply(content: str) -> bool:
    c = (content or "").lower()
    return "chill" in c or "vibezzzzz" in c or "chill vibezzzzz" in c


async def chat(db_path: str, user_id: int, user_message: str) -> str:
    if not GROQ_API_KEY:
        log.warning("AI offline — GROQ_API_KEY missing (user_id=%s)", user_id)
        return "⚠️ AI is offline — set `GROQ_API_KEY` on the server."

    personality = await get_personality(db_path)
    history = _memory[user_id]
    log.debug(
        "chat start user=%s hist=%d msg_len=%d model=%s",
        user_id, len(history), len(user_message or ""), GROQ_MODEL,
    )

    messages: List[dict] = [{"role": "system", "content": personality}]
    messages.extend(list(history))
    messages.append({"role": "user", "content": user_message[:2000]})

    import time
    t0 = time.perf_counter()
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
            ms = (time.perf_counter() - t0) * 1000
            if res.status_code != 200:
                detail = res.text[:200]
                log.error("Groq HTTP %s in %.0fms: %s", res.status_code, ms, detail)
                return f"⚠️ Groq error ({res.status_code}): {detail}"
            data = res.json()
            reply = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage") or {}
            log.info(
                "Groq OK user=%s %.0fms reply_len=%d prompt_tokens=%s completion_tokens=%s",
                user_id, ms, len(reply),
                usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"),
            )
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        log_exception(log, f"Groq request failed after {ms:.0f}ms", e)
        return f"⚠️ AI request failed: {type(e).__name__}: {e}"

    history.append({"role": "user", "content": user_message[:2000]})
    history.append({"role": "assistant", "content": reply})
    return reply


def clear_memory(user_id: int) -> None:
    _memory.pop(user_id, None)
