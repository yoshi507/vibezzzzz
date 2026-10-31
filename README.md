# Vibezzzzz

Discord bot (OwO-style economy + moderation + cats + music + dashboard).

## Run

1. Copy `.env.example` and set tokens
2. `pip install -r requirements.txt`
3. `python bot.py`

## Layout

- `bot.py` — main bot
- `web.py` — dashboard (FastAPI)
- `ai.py` — Groq AI
- `cats.py` — cat collection
- `extras.py` — counting, tickets, giveaways, AFK, summary, messages, say
- `moderation.py` — jail, ban, lock, etc.
- `security.py` — antinuke, antiraid, verify, autorole
- `music.py` — play / SoundCloud fallback
- `server_logs.py` — Circle-style event logs
- `logging_setup.py` — advanced logging
- `templates/` — dashboard HTML

Optional: `SYNC_GUILD_ID`, `YTDLP_COOKIES`, `GROQ_API_KEY`, `BOT_OWNER_IDS`
