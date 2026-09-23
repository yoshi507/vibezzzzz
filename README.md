# ✨ Vibezzzzz Discord Bot

A fun, OwO-inspired Discord economy bot with gambling features! Earn **vibes** ✨ by playing games, claiming dailies, and climbing the leaderboard.

## Features

- 💰 **Built-in Currency System** — Start with 100 vibes
- 🎰 **Slots** — Classic 3-reel slot machine with multipliers up to 50x
- 🎲 **Number Guessing** — Guess 1-10, correct = 5x payout
- 🧠 **Trivia** — Answer questions for free vibes
- ☀️ **Daily Rewards** — Claim 50-150 vibes every 24 hours
- 🏆 **Leaderboard** — See who's the richest viber
- Supports both **prefix commands** (`v!`) and **slash commands**

## Commands

| Command | Description |
|---------|-------------|
| `v!balance` / `/balance` | Check your (or someone's) vibes |
| `v!daily` / `/daily` | Claim daily reward |
| `v!slots <amount>` / `/slots` | Spin the slots |
| `v!guess <1-10> <amount>` | Guess the number |
| `v!trivia` | Play trivia for free vibes |
| `v!leaderboard` | Top 10 richest players |
| `v!help` | Show all commands |

## Setup

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a **New Application** → name it `vibezzzzz`
3. Go to **Bot** tab → **Add Bot**
4. Copy the **Token** (you'll need this)
5. Enable **Message Content Intent** under Privileged Gateway Intents
6. Go to **OAuth2 → URL Generator**
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Use Slash Commands`
7. Invite the bot to your server using the generated URL

### 2. Install & Run

```bash
# Clone the repo
git clone https://github.com/yoshi507/vibezzzzz.git
cd vibezzzzz

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env and paste your DISCORD_TOKEN

# Run the bot
python bot.py
```

### 3. Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------||
| `DISCORD_TOKEN` | ✅ Yes | Your Discord bot token | — |
| `BOT_PREFIX` | ❌ No | Command prefix | `v!` |

Set these on your server / hosting platform (Railway, Replit, VPS, etc.) as environment variables, **or** put them in a `.env` file (never commit `.env`!).

## Hosting Tips

- **Replit / Railway / Render / Fly.io**: Add `DISCORD_TOKEN` in the secrets / environment variables section.
- Keep the bot running 24/7 for the best experience.
- The database (`vibezzzzz.db`) is created automatically and stores all balances locally.

## License

MIT — feel free to modify and vibe with it! ✨

---

Made with 💜 for the vibes.
