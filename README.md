# ✨ Vibezzzzz Discord Bot + Dashboard

OwO-style economy bot with gambling + full web admin dashboard.

## Features

### Bot
- Currency: vibes ✨ (start with 100)
- Slots, number guess, trivia, daily, leaderboard
- Prefix (`v!`) + full slash commands
- Per-server toggles for gambling & trivia
- Admin: `addmoney` to give/remove vibes

### Dashboard (same process)
- Discord OAuth login
- Global stats & leaderboard
- Manage servers you admin
- Give vibes from the web UI

## Env vars

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token |
| `DISCORD_CLIENT_ID` | Dashboard | OAuth Client ID |
| `DISCORD_CLIENT_SECRET` | Dashboard | OAuth Client Secret |
| `DASHBOARD_REDIRECT_URI` | Dashboard | e.g. `http://IP:8080/callback` |
| `DASHBOARD_SECRET_KEY` | Dashboard | Random long string |
| `DASHBOARD_PORT` | ❌ | Default 8080 |
| `DASHBOARD_PUBLIC_URL` | ❌ | Shown in `/dashboard` command |

## Commands

| Command | Description |
|---------|-------------|
| `v!balance` / `/balance` | Check vibes |
| `v!daily` / `/daily` | Daily reward |
| `v!slots` / `/slots` | Slots |
| `v!guess` / `/guess` | Number guess |
| `v!trivia` / `/trivia` | Trivia |
| `v!leaderboard` / `/leaderboard` | Top players |
| `v!dashboard` / `/dashboard` | Dashboard link |
| `v!addmoney @user amt` / `/addmoney` | [Admin] Give/remove vibes |
| `v!help` / `/help` | Help |

Made with 💜
