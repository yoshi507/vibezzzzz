"""
Vibezzzzz advanced logging.

Features:
  - Colored console + rotating file logs
  - Separate error log file
  - Structured extra fields (user, guild, command, latency)
  - Command timing / performance
  - Correlation IDs per command invocation
  - Configurable level via LOG_LEVEL env (DEBUG/INFO/WARNING/ERROR)
  - Discord gateway + HTTP noise filtered unless DEBUG
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

# ── Paths ────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).resolve().parent
LOG_DIR = _BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

MAIN_LOG = LOG_DIR / "vibezzzzz.log"
ERROR_LOG = LOG_DIR / "errors.log"
COMMANDS_LOG = LOG_DIR / "commands.log"
AI_LOG = LOG_DIR / "ai.log"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "1") not in ("0", "false", "False", "no")
MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

# ── Colors (console only) ────────────────────────────────────────────────────
_COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
    "RESET": "\033[0m",
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, "")
        reset = _COLORS["RESET"]
        # Don't mutate the real levelname permanently
        original = record.levelname
        record.levelname = f"{color}{original}{reset}"
        try:
            return super().format(record)
        finally:
            record.levelname = original


class StructuredFormatter(logging.Formatter):
    """Adds extra context fields when present."""

    def format(self, record: logging.LogRecord) -> str:
        extras = []
        for key in ("cid", "v_user", "v_user_id", "v_guild", "v_guild_id", "v_command", "v_latency_ms", "v_channel"):
            val = getattr(record, key, None)
            if val is not None and val != "":
                extras.append(f"{key}={val}")
        base = super().format(record)
        if extras:
            return f"{base} | {' '.join(extras)}"
        return base


def _make_rotating(path: Path, level: int, fmt: logging.Formatter) -> logging.Handler:
    h = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    h.setLevel(level)
    h.setFormatter(fmt)
    return h


_configured = False


def setup_logging() -> logging.Logger:
    """Configure root + module loggers. Call once at startup."""
    global _configured
    if _configured:
        return logging.getLogger("vibezzzzz")

    level = getattr(logging, LOG_LEVEL, logging.INFO)

    file_fmt = StructuredFormatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = ColorFormatter(
        "%(asctime)s │ %(levelname)-8s │ %(name)-12s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers filter

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(console_fmt)
    root.addHandler(ch)

    if LOG_TO_FILE:
        # Main rotating log (INFO+)
        root.addHandler(_make_rotating(MAIN_LOG, logging.INFO, file_fmt))
        # Errors only
        root.addHandler(_make_rotating(ERROR_LOG, logging.ERROR, file_fmt))
        # Commands (dedicated logger handler)
        cmd_logger = logging.getLogger("vibezzzzz.commands")
        cmd_h = _make_rotating(COMMANDS_LOG, logging.DEBUG, file_fmt)
        cmd_logger.addHandler(cmd_h)
        cmd_logger.propagate = True
        # AI
        ai_logger = logging.getLogger("vibezzzzz.ai")
        ai_h = _make_rotating(AI_LOG, logging.DEBUG, file_fmt)
        ai_logger.addHandler(ai_h)
        ai_logger.propagate = True

    # Quiet noisy libraries unless DEBUG
    if level > logging.DEBUG:
        for noisy in ("discord", "discord.gateway", "discord.http", "discord.client",
                      "aiosqlite", "httpx", "httpcore", "uvicorn", "uvicorn.access",
                      "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        logging.getLogger("discord").setLevel(logging.INFO)
        logging.getLogger("httpx").setLevel(logging.INFO)

    _configured = True
    log = logging.getLogger("vibezzzzz")
    log.info(
        "Logging online | level=%s file=%s dir=%s",
        LOG_LEVEL, LOG_TO_FILE, LOG_DIR,
    )
    return log


def get_logger(name: str = "vibezzzzz") -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)


def new_cid() -> str:
    """Short correlation id for one command/request."""
    return uuid.uuid4().hex[:8]


def ctx_extra(
    *,
    cid: str = "",
    user: Any = None,
    guild: Any = None,
    command: str = "",
    latency_ms: Optional[float] = None,
    channel: Any = None,
) -> dict:
    """Build logging extra dict. Uses v_ prefix to avoid LogRecord clashes."""
    extra: dict = {}
    if cid:
        extra["cid"] = str(cid)
    if user is not None:
        extra["v_user"] = str(getattr(user, "display_name", None) or getattr(user, "name", user))
        extra["v_user_id"] = str(getattr(user, "id", ""))
    if guild is not None:
        extra["v_guild"] = str(getattr(guild, "name", guild))
        extra["v_guild_id"] = str(getattr(guild, "id", ""))
    if command:
        extra["v_command"] = str(command)
    if latency_ms is not None:
        extra["v_latency_ms"] = round(float(latency_ms), 1)
    if channel is not None:
        extra["v_channel"] = str(getattr(channel, "name", getattr(channel, "id", channel)))
    return extra


@contextmanager
def timed(logger: logging.Logger, label: str, level: int = logging.DEBUG, **extra_kw):
    """Context manager that logs duration of a block."""
    start = time.perf_counter()
    cid = extra_kw.get("cid", "")
    try:
        yield
    finally:
        ms = (time.perf_counter() - start) * 1000
        logger.log(
            level,
            "%s finished in %.1fms",
            label,
            ms,
            extra=ctx_extra(latency_ms=ms, **{k: v for k, v in extra_kw.items() if k != "latency_ms"}),
        )


def log_exception(logger: logging.Logger, msg: str, exc: BaseException, **extra_kw) -> None:
    logger.error(
        "%s: %s: %s\n%s",
        msg,
        type(exc).__name__,
        exc,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        extra=ctx_extra(**extra_kw),
    )
