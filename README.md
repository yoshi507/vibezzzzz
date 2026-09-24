# ✨ Vibezzzzz Discord Bot + Dashboard

A fun OwO-inspired Discord economy bot with gambling features **and** a full web admin dashboard.

## Features

### Bot
- 💰 Built-in currency (`vibes` ✨) — start with 100
- 🎰 Spots (up to 50x jackpot)
- 🎲 Number guessing (5x payout)
- 🧠 Trivia for free vibes
- ☀️ Daily rewards
- 🏆 Leaderboard
- Prefix (`v!`) + full slash command support
- Per-server toggles for gambling & trivia (via dashboard)
- Admin give/remove money command

### Dashboard (same process)
- Discord OAuth login
- Global stats & leaderboard
- Manage servers you admin
- Give/remove vibes from the web UI

## Commands

| Prefix | Slash | Description |
|--------|-------|-------------|
| `v!balance` | `/balance` | Check vibes |
| `v!daily` | `/daily` | Daily reward |
| `v!slots <amt>` | `/slots` | Slots |
| `v!guess <1-10> <amt>` | `/guess` | Number guess |
| `v!trivia` | `/trivia` | Trivia |
| `v!leaderboard` | `/leaderboard` | Top players |
| `v!dashboard` | `/dashboard` | Dashboard link |
| `v!addmoney @user <amt>` | `/addmoney` | **[Admin]** Give/remove vibes |
| `v!help` | `/help` | Help |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token |
| `BOT_PREFIX` | ❌ | Default `v!` |
| `DISCORD_CLIENT_ID` | Dashboard | OAuth Client ID |
| `DISCORD_CLIENT_SECRET` | Dashboard | OAuth Client Secret |
| `DASHBOARD_REDIRECT_URI` | Dashboard | e.g. `http://IP:8080/callback` |
| `DASHBOARD_SECRET_KEY` | Dashboard | Random long string |
| `DASHBOARD_PORT` | ❌ | Default `8080` |
| `DASHBOARD_PUBLIC_URL` | ❌ | Shown by dashboard command |

## Setup

1. Enable **Message Content** + **Server Members** intents
2. Invite with `bot` + `applications.commands` scopes
3. For dashboard: add OAuth redirect in Developer Portal
4. `pip install -r requirements.txt && python bot.py`

Made with 💜 for the vibes.
