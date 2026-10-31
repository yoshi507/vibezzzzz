import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import random
import asyncio
import time
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path

# Ensure local modules (ai.py, web.py) resolve when cwd differs from bot folder
import sys
_BOT_DIR = str(Path(__file__).resolve().parent)
if _BOT_DIR not in sys.path:
    sys.path.insert(0, _BOT_DIR)


load_dotenv()

from logging_setup import setup_logging, get_logger, new_cid, ctx_extra, timed, log_exception

log = setup_logging()
cmd_log = get_logger("vibezzzzz.commands")

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "v!")
DASHBOARD_PORT = int(os.getenv("SERVER_PORT") or os.getenv("DASHBOARD_PORT") or "8080")
OWNER_IDS = {int(x.strip()) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}
SYNC_GUILD_ID = int(x) if (x := os.getenv("SYNC_GUILD_ID", "").strip()) and x.isdigit() else None

DB_PATH = str(Path(__file__).resolve().parent / "vibezzzzz.db")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX, "v.", "vibez ", "vibe "),
    intents=intents,
    help_command=None,
    max_messages=5000,  # better message-delete log content
)

CURRENCY = "vibes"
CURRENCY_EMOJI = "✨"
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
SLOT_WEIGHTS = [30, 25, 20, 15, 8, 2]
TRIVIA_QUESTIONS = [
    {"q": "What is the capital of France?", "a": "paris"},
    {"q": "What is 2 + 2?", "a": "4"},
    {"q": "What planet is known as the Red Planet?", "a": "mars"},
    {"q": "Who painted the Mona Lisa?", "a": "leonardo da vinci"},
    {"q": "What is the largest ocean on Earth?", "a": "pacific"},
    {"q": "How many continents are there?", "a": "7"},
    {"q": "What is the chemical symbol for water?", "a": "h2o"},
    {"q": "Who wrote 'Romeo and Juliet'?", "a": "shakespeare"},
    {"q": "What is the speed of light (approx km/s)?", "a": "300000"},
    {"q": "What year did World War 2 end?", "a": "1945"},
]


def is_bot_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def can_manage_money(member: discord.Member | discord.User) -> bool:
    # Only BOT_OWNER_IDS can use addmoney
    return is_bot_owner(member.id)



async def defer_thinking(interaction: discord.Interaction, ephemeral: bool = False):
    """Must be called within 3s so Discord shows 'Vibezzzzz is thinking…'."""
    try:
        if not getattr(interaction, "_log_cid", None):
            interaction._log_cid = new_cid()
            interaction._log_start = time.perf_counter()
            cmd = interaction.command.qualified_name if interaction.command else "?"
            cmd_log.info(
                "SLASH  ▶ /%s",
                cmd,
                extra=ctx_extra(
                    cid=interaction._log_cid, user=interaction.user, guild=interaction.guild,
                    command=f"/{cmd}", channel=interaction.channel,
                ),
            )
    except Exception:
        pass
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        except Exception as e:
            try:
                log.warning("Defer failed: %s", e)
            except Exception:
                pass


async def reply(interaction: discord.Interaction, *args, **kwargs):
    """Send followup after defer (or respond if somehow not deferred)."""
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)
    except Exception as e:
        try:
            return await interaction.followup.send(f"⚠️ Something went wrong: {e}", ephemeral=True)
        except Exception:
            pass


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 100,
                last_daily TEXT, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT 'v!',
                gambling_enabled INTEGER DEFAULT 1, trivia_enabled INTEGER DEFAULT 1)"""
        )
        await db.commit()
    try:
        from ai import ensure_ai_tables
        await ensure_ai_tables(DB_PATH)
    except Exception as e:
        log.warning("AI tables setup: %s", e)
    try:
        from server_logs import ensure_log_columns
        await ensure_log_columns(DB_PATH)
    except Exception as e:
        log.warning("Server log tables: %s", e)


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT balance, last_daily, wins, losses FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                await db.execute("INSERT INTO users (user_id, balance) VALUES (?, 100)", (user_id,))
                await db.commit()
                return 100, None, 0, 0
            return row


async def update_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def set_daily(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_daily = ? WHERE user_id = ?",
            (datetime.utcnow().isoformat(), user_id),
        )
        await db.commit()


async def add_win_loss(user_id: int, won: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        col = "wins" if won else "losses"
        await db.execute(f"UPDATE users SET {col} = {col} + 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def is_feature_enabled(guild_id, feature: str) -> bool:
    if guild_id is None:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT gambling_enabled, trivia_enabled FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return True
            return bool(row[0] if feature == "gambling" else row[1])


async def start_dashboard():
    try:
        import sys
        import uvicorn
        if not (Path(_BOT_DIR) / "web.py").is_file():
            log.warning("Dashboard skipped: web.py not found in %s", _BOT_DIR)
            return
        from web import app as web_app, set_bot

        set_bot(bot)
        config = uvicorn.Config(
            web_app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning", access_log=False
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = False
        log.info("🌐 Dashboard starting on 0.0.0.0:%s", DASHBOARD_PORT)
        await server.serve()
    except SystemExit:
        log.warning("Dashboard could not bind port %s — bot still online", DASHBOARD_PORT)
    except OSError as e:
        log.warning("Dashboard bind error: %s", e)
    except ImportError as e:
        log.warning("Dashboard packages missing: %s", e)
    except Exception as e:
        log_exception(log, "Dashboard error", e)



@bot.event
async def on_command(ctx):
    try:
        cid = new_cid()
        ctx._log_cid = cid
        ctx._log_start = time.perf_counter()
        cmd_log.info(
            "PREFIX ▶ %s%s",
            ctx.prefix or "",
            ctx.command.qualified_name if ctx.command else "?",
            extra=ctx_extra(
                cid=cid, user=ctx.author, guild=ctx.guild,
                command=ctx.command.qualified_name if ctx.command else "?",
                channel=ctx.channel,
            ),
        )
    except Exception:
        pass  # never block commands because of logging


@bot.event
async def on_command_completion(ctx):
    try:
        cid = getattr(ctx, "_log_cid", "")
        start = getattr(ctx, "_log_start", None)
        ms = (time.perf_counter() - start) * 1000 if start else None
        cmd_log.info(
            "PREFIX ✓ %s",
            ctx.command.qualified_name if ctx.command else "?",
            extra=ctx_extra(
                cid=cid, user=ctx.author, guild=ctx.guild,
                command=ctx.command.qualified_name if ctx.command else "?",
                latency_ms=ms, channel=ctx.channel,
            ),
        )
    except Exception:
        pass


@bot.event
async def on_command_error(ctx, error):
    # Unwrap CommandInvokeError so we see the real cause
    orig = getattr(error, "original", error)
    cid = getattr(ctx, "_log_cid", new_cid())
    cmd_name = getattr(ctx.command, "qualified_name", "?")

    if isinstance(error, (commands.CommandOnCooldown, commands.MissingRequiredArgument,
                          commands.BadArgument, commands.CommandNotFound,
                          commands.CheckFailure, commands.MissingPermissions)):
        try:
            cmd_log.warning("PREFIX ✗ %s: %s", cmd_name, type(error).__name__)
        except Exception:
            pass
        return

    try:
        log_exception(cmd_log, f"PREFIX command error ({cmd_name})", orig, cid=cid, user=ctx.author, guild=ctx.guild, command=cmd_name)
    except Exception:
        print(f"PREFIX error in {cmd_name}: {type(orig).__name__}: {orig}")

    # Tell the user the REAL error (not just CommandInvokeError)
    detail = f"{type(orig).__name__}: {orig}"
    if len(detail) > 300:
        detail = detail[:300] + "…"
    try:
        await ctx.send(f"⚠️ **Command failed:** `{detail}`")
    except Exception:
        pass


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
    cid = getattr(interaction, "_log_cid", "")
    start = getattr(interaction, "_log_start", None)
    ms = (time.perf_counter() - start) * 1000 if start else None
    cmd_log.info(
        "SLASH  ✓ /%s",
        command.qualified_name if command else "?",
        extra=ctx_extra(
            cid=cid, user=interaction.user, guild=interaction.guild,
            command=f"/{command.qualified_name}" if command else "?",
            latency_ms=ms, channel=interaction.channel,
        ),
    )


@bot.event
async def on_guild_join(guild):
    log.info("Joined guild: %s (%s) members=%s", guild.name, guild.id, guild.member_count)


@bot.event
async def on_guild_remove(guild):
    log.info("Left guild: %s (%s)", guild.name, guild.id)


@bot.event
async def on_ready():
    log.info("on_ready fired | user=%s id=%s guilds=%s latency=%.0fms",
             bot.user, bot.user.id if bot.user else "?", len(bot.guilds), bot.latency * 1000)
    await init_db()
    log.info("Database ready at %s", DB_PATH)
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.playing, name="with vibes ✨ | v!help"),
    )
    log.info("✨ %s is online and ready to vibe! ✨", bot.user)
    if OWNER_IDS:
        log.info("Bot owners configured: %s", OWNER_IDS)
    else:
        log.warning("BOT_OWNER_IDS is empty — addmoney/admin locked to nobody")

    # Register feature modules ONCE (commands must exist before any sync)
    if not getattr(bot, "_modules_loaded", False):
        bot._modules_loaded = True
        try:
            from server_logs import register_server_log_events
            register_server_log_events(bot, DB_PATH)
            log.info("Server logging armed")
        except Exception as e:
            log.warning("Server log register failed: %s", e)
        try:
            from moderation import register_moderation, ensure_mod_tables
            await ensure_mod_tables(DB_PATH)
            register_moderation(bot, DB_PATH)
            log.info("Moderation armed")
        except Exception as e:
            log.warning("Moderation register failed: %s", e)
        try:
            from cats import register_cats, ensure_cat_tables
            await ensure_cat_tables(DB_PATH)
            register_cats(bot, DB_PATH)
            log.info("Cats armed")
        except Exception as e:
            log.warning("Cats register failed: %s", e)
        try:
            from extras import register_extras, ensure_extra_tables
            await ensure_extra_tables(DB_PATH)
            register_extras(bot, DB_PATH)
            log.info("Extras armed")
        except Exception as e:
            log.warning("Extras register failed: %s", e)
        try:
            from security import register_security, ensure_security_tables
            await ensure_security_tables(DB_PATH)
            register_security(bot, DB_PATH)
            log.info("Security armed")
        except Exception as e:
            log.warning("Security register failed: %s", e)
        try:
            from music import register_music
            register_music(bot)
            log.info("Music armed")
        except Exception as e:
            log.warning("Music register failed: %s", e)

    # ONE slash sync only — repeated global syncs cause rate limits
    if not getattr(bot, "_slash_synced", False):
        bot._slash_synced = True
        await _sync_slash_commands()

    if not getattr(bot, "_dashboard_started", False):
        bot._dashboard_started = True
        log.info("Starting dashboard task on port %s", DASHBOARD_PORT)
        asyncio.create_task(start_dashboard())


async def _sync_slash_commands():
    """Sync slash commands once. Prefer guild sync to avoid global rate limits."""
    try:
        if SYNC_GUILD_ID:
            guild = discord.Object(id=SYNC_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            log.info(
                "Slash commands synced to guild %s (%d cmds) — instant. "
                "Unset SYNC_GUILD_ID later for global sync.",
                SYNC_GUILD_ID, len(synced),
            )
            return
        # Global sync can take up to 1 hour to appear and is easy to rate-limit
        synced = await bot.tree.sync()
        log.info("Global slash sync OK: %d commands (may take up to ~1h to show everywhere)", len(synced))
    except discord.HTTPException as e:
        if e.status == 429:
            retry = getattr(e, "retry_after", None) or 60
            log.warning(
                "Slash sync RATE LIMITED (429). Waiting is normal with many commands. "
                "retry_after≈%ss. Set SYNC_GUILD_ID=<your_server_id> for instant guild sync.",
                retry,
            )
        else:
            log.warning("Slash sync HTTP error: %s", e)
    except Exception as e:
        log_exception(log, "Slash command sync failed", e)


# ─── Prefix commands ─────────────────────────────────────────────────────────

@bot.command(name="balance", aliases=["bal", "vibes"])
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    bal, _, wins, losses = await get_user(member.id)
    embed = discord.Embed(title=f"{CURRENCY_EMOJI} {member.display_name}'s Vibes", color=discord.Color.purple())
    embed.add_field(name="Balance", value=f"**{bal:,}** {CURRENCY}", inline=True)
    embed.add_field(name="Wins", value=f"**{wins}**", inline=True)
    embed.add_field(name="Losses", value=f"**{losses}**", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="daily")
@commands.cooldown(1, 86400, commands.BucketType.user)
async def daily(ctx):
    bal, _, _, _ = await get_user(ctx.author.id)
    reward = random.randint(50, 150)
    await update_balance(ctx.author.id, reward)
    await set_daily(ctx.author.id)
    embed = discord.Embed(
        title="☀️ Daily Vibes Claimed!",
        description=f"You received **{reward}** {CURRENCY} {CURRENCY_EMOJI}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="New Balance", value=f"{bal + reward:,} {CURRENCY}")
    await ctx.send(embed=embed)


@daily.error
async def daily_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        h, m = int(error.retry_after // 3600), int((error.retry_after % 3600) // 60)
        await ctx.send(f"⏳ Come back in **{h}h {m}m**.")


@bot.command(name="slots")
@commands.cooldown(1, 3, commands.BucketType.user)
async def slots(ctx, amount: int):
    if not await is_feature_enabled(ctx.guild.id if ctx.guild else None, "gambling"):
        return await ctx.send("🚫 Gambling is disabled on this server.")
    if amount <= 0:
        return await ctx.send("❌ Bet must be > 0!")
    bal, _, _, _ = await get_user(ctx.author.id)
    if amount > bal:
        return await ctx.send(f"❌ You only have **{bal}** {CURRENCY}!")
    await update_balance(ctx.author.id, -amount)
    r1, r2, r3 = [random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0] for _ in range(3)]
    mult = (
        50 if r1 == r2 == r3 == "7️⃣"
        else 20 if r1 == r2 == r3 == "💎"
        else 10 if r1 == r2 == r3
        else 2 if r1 == r2 or r2 == r3 or r1 == r3
        else 0
    )
    win = amount * mult
    embed = discord.Embed(
        title="🎰 Vibezzzzz Slots", description=f"**| {r1} | {r2} | {r3} |**", color=discord.Color.purple()
    )
    if mult:
        await update_balance(ctx.author.id, win)
        await add_win_loss(ctx.author.id, True)
        embed.add_field(name="🎉 YOU WON!", value=f"**+{win:,}** {CURRENCY} (x{mult})")
        embed.color = discord.Color.green()
    else:
        await add_win_loss(ctx.author.id, False)
        embed.add_field(name="💔 You lost...", value=f"-{amount:,} {CURRENCY}")
        embed.color = discord.Color.red()
    new_bal, _, _, _ = await get_user(ctx.author.id)
    embed.set_footer(text=f"New balance: {new_bal:,} {CURRENCY}")
    await ctx.send(embed=embed)


@slots.error
async def slots_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Usage: `{PREFIX}slots <amount>`")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down! {error.retry_after:.1f}s")


@bot.command(name="guess")
@commands.cooldown(1, 5, commands.BucketType.user)
async def guess(ctx, number: int, amount: int):
    if not await is_feature_enabled(ctx.guild.id if ctx.guild else None, "gambling"):
        return await ctx.send("🚫 Gambling is disabled on this server.")
    if not 1 <= number <= 10 or amount <= 0:
        return await ctx.send("❌ Number 1-10 and bet > 0 required")
    bal, _, _, _ = await get_user(ctx.author.id)
    if amount > bal:
        return await ctx.send(f"❌ You only have **{bal}** {CURRENCY}!")
    await update_balance(ctx.author.id, -amount)
    secret = random.randint(1, 10)
    embed = discord.Embed(title="🎲 Number Guess", color=discord.Color.purple())
    embed.add_field(name="Your Guess", value=str(number), inline=True)
    embed.add_field(name="The Number", value=str(secret), inline=True)
    if number == secret:
        await update_balance(ctx.author.id, amount * 5)
        await add_win_loss(ctx.author.id, True)
        embed.description = f"🎉 **CORRECT!** Won **{amount * 5:,}** {CURRENCY}!"
        embed.color = discord.Color.green()
    else:
        await add_win_loss(ctx.author.id, False)
        embed.description = f"💔 Wrong! Lost **{amount:,}** {CURRENCY}."
        embed.color = discord.Color.red()
    new_bal, _, _, _ = await get_user(ctx.author.id)
    embed.set_footer(text=f"New balance: {new_bal:,} {CURRENCY}")
    await ctx.send(embed=embed)


@guess.error
async def guess_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send(f"❌ Usage: `{PREFIX}guess <1-10> <bet>`")


@bot.command(name="trivia")
@commands.cooldown(1, 10, commands.BucketType.user)
async def trivia(ctx):
    if not await is_feature_enabled(ctx.guild.id if ctx.guild else None, "trivia"):
        return await ctx.send("🚫 Trivia is disabled on this server.")
    q = random.choice(TRIVIA_QUESTIONS)
    await ctx.send(
        embed=discord.Embed(title="🧠 Trivia Time!", description=q["q"], color=discord.Color.blue()).set_footer(
            text="15 seconds!"
        )
    )
    try:
        msg = await bot.wait_for(
            "message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=15.0
        )
        if q["a"] in msg.content.lower():
            reward = random.randint(25, 75)
            await update_balance(ctx.author.id, reward)
            await add_win_loss(ctx.author.id, True)
            await ctx.send(f"✅ Correct! **{reward}** {CURRENCY} {CURRENCY_EMOJI}")
        else:
            await add_win_loss(ctx.author.id, False)
            await ctx.send(f"❌ Wrong! Answer: **{q['a']}**")
    except asyncio.TimeoutError:
        await ctx.send(f"⏰ Time's up! Answer: **{q['a']}**")


@bot.command(name="leaderboard", aliases=["lb", "top"])
async def leaderboard(ctx):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10") as cur:
            rows = await cur.fetchall()
    if not rows:
        return await ctx.send("No one has vibes yet!")
    desc = ""
    for i, (uid, bal) in enumerate(rows, 1):
        u = bot.get_user(uid) or await bot.fetch_user(uid)
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"**{i}.**"
        desc += f"{medal} {u.display_name if u else 'Unknown'} — **{bal:,}** {CURRENCY}\n"
    await ctx.send(embed=discord.Embed(title="🏆 Top Vibers", description=desc, color=discord.Color.gold()))


@bot.command(name="dashboard", aliases=["dash", "panel"])
async def dashboard_cmd(ctx):
    public = os.getenv("DASHBOARD_PUBLIC_URL", f"http://YOUR_SERVER_IP:{DASHBOARD_PORT}")
    embed = discord.Embed(
        title="🌐 Vibezzzzz Dashboard",
        description=f"**{public}**\n\nStats • Leaderboard • Server settings • Give vibes",
        color=discord.Color.purple(),
    )
    await ctx.send(embed=embed)


@bot.command(name="addmoney", aliases=["givemoney", "addvibes"])
async def addmoney(ctx, member: discord.Member, amount: int):
    if not can_manage_money(ctx.author):
        return await ctx.send("❌ Only bot owners can use this command (`BOT_OWNER_IDS`).")
    if amount == 0:
        return await ctx.send("❌ Amount can't be 0!")
    await get_user(member.id)
    await update_balance(member.id, amount)
    new_bal, _, _, _ = await get_user(member.id)
    action = "gave" if amount > 0 else "removed"
    embed = discord.Embed(
        title="💰 Admin Money Update",
        description=f"{ctx.author.mention} {action} **{abs(amount):,}** {CURRENCY} {'to' if amount > 0 else 'from'} {member.mention}",
        color=discord.Color.green() if amount > 0 else discord.Color.orange(),
    )
    embed.add_field(name="New Balance", value=f"**{new_bal:,}** {CURRENCY}")
    await ctx.send(embed=embed)


@addmoney.error
async def addmoney_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send(f"❌ Usage: `{PREFIX}addmoney @user <amount>`")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="✨ Vibezzzzz — All Commands",
        description=f"Prefix `{PREFIX}` · every command also has `/slash`",
        color=discord.Color.purple(),
    )
    embed.add_field(name="💰 Economy", value="`balance` `daily` `leaderboard` `dashboard` `addmoney`", inline=False)
    embed.add_field(name="🎰 Games", value="`slots` `guess` `trivia`", inline=False)
    embed.add_field(name="🐈 Cats", value="`pack` `inventory` `catnip` `dailycat` `catlb` `battlepass`", inline=False)
    embed.add_field(name="🤖 AI", value="`ask` `forget`", inline=False)
    embed.add_field(name="📋 Logs", value="`setlog` `unsetlog` `logstatus`", inline=False)
    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "`setupmod` `lock` `unlock` `lockall` `unlockall` `hide` `unhide` "
            "`jail` `unjail` `jaillist` `ban` `softban` `unban` `kick` "
            "`timeout` `untimeout` `warn` `warnings` `clearwarns` `purge` `slowmode` `nick`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎁 Server",
        value=(
            "`setcount` `count` `sethoneypot` `unsethoneypot` "
            "`setwelcome` `testwelcome` `ticketpanel` `close` "
            "`gstart` `gend` `greroll`"
        ),
        inline=False,
    )
    embed.add_field(name="💤 AFK & activity", value="`afk` `unafk` `summary` `messages`", inline=False)
    embed.add_field(
        name="🛡️ Security",
        value="`antinuke` `antiraid` `raidsettings` `whitelist` `unwhitelist` `setupverify` `verifyoff` `autorole` `autoroleoff`",
        inline=False,
    )
    embed.add_field(name="🎶 Music", value="`play` `skip` `stop` `queue` `join` `leave` `pause` `resume` (YT/SoundCloud/Spotify)", inline=False)
    embed.add_field(name="📢 Utility", value="`say <message>` — bot posts your text (Manage Messages)", inline=False)
    embed.set_footer(text="Dashboard: welcome, counting, honeypot, logs, AI personality")
    await ctx.send(embed=embed)


# ─── Slash commands (defer first → shows "thinking…", avoids 3s timeout) ─────

@bot.tree.command(name="balance", description="Check vibe balance ✨")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    await defer_thinking(interaction)
    member = member or interaction.user
    bal, _, wins, losses = await get_user(member.id)
    embed = discord.Embed(title=f"{CURRENCY_EMOJI} {member.display_name}'s Vibes", color=discord.Color.purple())
    embed.add_field(name="Balance", value=f"**{bal:,}**", inline=True)
    embed.add_field(name="Wins", value=f"**{wins}**", inline=True)
    embed.add_field(name="Losses", value=f"**{losses}**", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="slots", description="Spin the slots! 🎰")
@app_commands.describe(amount="Bet amount")
async def slash_slots(interaction: discord.Interaction, amount: int):
    await defer_thinking(interaction)
    if not await is_feature_enabled(interaction.guild_id, "gambling"):
        return await interaction.followup.send("🚫 Gambling disabled.", ephemeral=True)
    if amount <= 0:
        return await interaction.followup.send("❌ Bet > 0", ephemeral=True)
    bal, _, _, _ = await get_user(interaction.user.id)
    if amount > bal:
        return await interaction.followup.send(f"❌ Only **{bal}** vibes", ephemeral=True)
    await update_balance(interaction.user.id, -amount)
    r1, r2, r3 = [random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0] for _ in range(3)]
    mult = (
        50 if r1 == r2 == r3 == "7️⃣"
        else 20 if r1 == r2 == r3 == "💎"
        else 10 if r1 == r2 == r3
        else 2 if r1 == r2 or r2 == r3 or r1 == r3
        else 0
    )
    win = amount * mult
    embed = discord.Embed(title="🎰 Slots", description=f"**| {r1} | {r2} | {r3} |**", color=discord.Color.purple())
    if mult:
        await update_balance(interaction.user.id, win)
        await add_win_loss(interaction.user.id, True)
        embed.add_field(name="🎉 WON!", value=f"+{win:,} (x{mult})")
        embed.color = discord.Color.green()
    else:
        await add_win_loss(interaction.user.id, False)
        embed.add_field(name="💔 Lost", value=f"-{amount:,}")
        embed.color = discord.Color.red()
    new_bal, _, _, _ = await get_user(interaction.user.id)
    embed.set_footer(text=f"Balance: {new_bal:,}")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="daily", description="Claim daily vibes ☀️")
async def slash_daily(interaction: discord.Interaction):
    await defer_thinking(interaction)
    bal, last, _, _ = await get_user(interaction.user.id)
    if last and datetime.utcnow() - datetime.fromisoformat(last) < timedelta(hours=24):
        rem = timedelta(hours=24) - (datetime.utcnow() - datetime.fromisoformat(last))
        return await interaction.followup.send(
            f"⏳ {int(rem.total_seconds() // 3600)}h {int((rem.total_seconds() % 3600) // 60)}m left",
            ephemeral=True,
        )
    reward = random.randint(50, 150)
    await update_balance(interaction.user.id, reward)
    await set_daily(interaction.user.id)
    await interaction.followup.send(
        embed=discord.Embed(title="☀️ Daily!", description=f"+**{reward}** {CURRENCY}", color=discord.Color.gold())
    )


@bot.tree.command(name="guess", description="Guess 1-10 for 5x 🎲")
@app_commands.describe(number="1-10", amount="Bet")
async def slash_guess(interaction: discord.Interaction, number: int, amount: int):
    await defer_thinking(interaction)
    if not await is_feature_enabled(interaction.guild_id, "gambling"):
        return await interaction.followup.send("🚫 Disabled", ephemeral=True)
    if not 1 <= number <= 10 or amount <= 0:
        return await interaction.followup.send("❌ Invalid", ephemeral=True)
    bal, _, _, _ = await get_user(interaction.user.id)
    if amount > bal:
        return await interaction.followup.send(f"❌ Only {bal}", ephemeral=True)
    await update_balance(interaction.user.id, -amount)
    secret = random.randint(1, 10)
    embed = discord.Embed(title="🎲 Guess", color=discord.Color.purple())
    embed.add_field(name="Guess", value=str(number), inline=True)
    embed.add_field(name="Number", value=str(secret), inline=True)
    if number == secret:
        await update_balance(interaction.user.id, amount * 5)
        await add_win_loss(interaction.user.id, True)
        embed.description = f"🎉 Won **{amount * 5:,}**!"
        embed.color = discord.Color.green()
    else:
        await add_win_loss(interaction.user.id, False)
        embed.description = f"💔 Lost **{amount:,}**"
        embed.color = discord.Color.red()
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="trivia", description="Trivia for free vibes 🧠")
async def slash_trivia(interaction: discord.Interaction):
    await defer_thinking(interaction)
    if not await is_feature_enabled(interaction.guild_id, "trivia"):
        return await interaction.followup.send("🚫 Disabled", ephemeral=True)
    q = random.choice(TRIVIA_QUESTIONS)
    await interaction.followup.send(
        embed=discord.Embed(title="🧠 Trivia", description=q["q"], color=discord.Color.blue()).set_footer(
            text="15 seconds!"
        )
    )
    try:
        msg = await bot.wait_for(
            "message",
            check=lambda m: m.author == interaction.user and m.channel == interaction.channel,
            timeout=15.0,
        )
        if q["a"] in msg.content.lower():
            reward = random.randint(25, 75)
            await update_balance(interaction.user.id, reward)
            await interaction.followup.send(f"✅ +**{reward}** {CURRENCY}")
        else:
            await interaction.followup.send(f"❌ Answer: **{q['a']}**")
    except asyncio.TimeoutError:
        await interaction.followup.send(f"⏰ Answer: **{q['a']}**")


@bot.tree.command(name="leaderboard", description="Top vibers 🏆")
async def slash_leaderboard(interaction: discord.Interaction):
    await defer_thinking(interaction)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10") as cur:
            rows = await cur.fetchall()
    if not rows:
        return await interaction.followup.send("No players yet")
    desc = ""
    for i, (uid, bal) in enumerate(rows, 1):
        u = bot.get_user(uid) or await bot.fetch_user(uid)
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"**{i}.**"
        desc += f"{medal} {u.display_name if u else '?'} — **{bal:,}**\n"
    await interaction.followup.send(
        embed=discord.Embed(title="🏆 Leaderboard", description=desc, color=discord.Color.gold())
    )


@bot.tree.command(name="dashboard", description="Dashboard link 🌐")
async def slash_dashboard(interaction: discord.Interaction):
    await defer_thinking(interaction, ephemeral=True)
    public = os.getenv("DASHBOARD_PUBLIC_URL", f"http://YOUR_SERVER_IP:{DASHBOARD_PORT}")
    await interaction.followup.send(
        embed=discord.Embed(title="🌐 Dashboard", description=f"**{public}**", color=discord.Color.purple())
    )


@bot.tree.command(name="addmoney", description="[Admin/Owner] Give or remove vibes")
@app_commands.describe(member="Member", amount="Amount (+ give / - remove)")
async def slash_addmoney(interaction: discord.Interaction, member: discord.Member, amount: int):
    await defer_thinking(interaction)
    if not can_manage_money(interaction.user):
        return await interaction.followup.send(
            "❌ Only bot owners can use this command (`BOT_OWNER_IDS`).", ephemeral=True
        )
    if amount == 0:
        return await interaction.followup.send("❌ Amount can't be 0", ephemeral=True)
    await get_user(member.id)
    await update_balance(member.id, amount)
    new_bal, _, _, _ = await get_user(member.id)
    action = "gave" if amount > 0 else "removed"
    embed = discord.Embed(
        title="💰 Admin Update",
        description=f"{interaction.user.mention} {action} **{abs(amount):,}** {'to' if amount > 0 else 'from'} {member.mention}",
        color=discord.Color.green() if amount > 0 else discord.Color.orange(),
    )
    embed.add_field(name="New Balance", value=f"**{new_bal:,}**")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="help", description="Show all commands")
async def slash_help(interaction: discord.Interaction):
    await defer_thinking(interaction, ephemeral=True)
    embed = discord.Embed(
        title="✨ Vibezzzzz — All Commands",
        description="Every feature has a `/` command",
        color=discord.Color.purple(),
    )
    embed.add_field(name="💰 Economy", value="`/balance` `/daily` `/leaderboard` `/dashboard` `/addmoney`", inline=False)
    embed.add_field(name="🎰 Games", value="`/slots` `/guess` `/trivia`", inline=False)
    embed.add_field(name="🐈 Cats", value="`/pack` `/inventory` `/catnip` `/dailycat` `/catlb` `/battlepass`", inline=False)
    embed.add_field(name="🤖 AI", value="`/ask` `/forget`", inline=False)
    embed.add_field(name="📋 Logs", value="`/setlog` `/unsetlog` `/logstatus`", inline=False)
    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "`/setupmod` `/lock` `/unlock` `/lockall` `/unlockall` `/hide` `/unhide` "
            "`/jail` `/unjail` `/jaillist` `/ban` `/softban` `/unban` `/kick` "
            "`/timeout` `/untimeout` `/warn` `/warnings` `/clearwarns` `/purge` `/slowmode` `/nick`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎁 Server",
        value=(
            "`/setcount` `/count` `/sethoneypot` `/unsethoneypot` "
            "`/setwelcome` `/testwelcome` `/ticketpanel` `/close` "
            "`/gstart` `/gend` `/greroll`"
        ),
        inline=False,
    )
    embed.add_field(name="💤 AFK & activity", value="`/afk` `/unafk` `/summary` `/messages`", inline=False)
    embed.add_field(
        name="🛡️ Security",
        value="`/antinuke` `/antiraid` `/raidsettings` `/whitelist` `/unwhitelist` `/setupverify` `/verifyoff` `/autorole` `/autoroleoff`",
        inline=False,
    )
    embed.add_field(name="🎶 Music", value="`/play` `/skip` `/stop` `/queue` `/join` `/leave` `/pause` `/resume`", inline=False)
    embed.add_field(name="📢 Utility", value="`/say`", inline=False)
    await interaction.followup.send(embed=embed)



# ─── AI (Groq) ───────────────────────────────────────────────────────────────

@bot.command(name="ask")
async def ask_cmd(ctx, *, question: str = None):
    if not question:
        return await ctx.send(f"❌ Usage: `{PREFIX}ask <question>`")
    async with ctx.typing():
        try:
            from ai import chat
            reply = await chat(DB_PATH, ctx.author.id, question)
        except Exception as e:
            reply = f"⚠️ AI error: {e}"
    for i in range(0, len(reply), 1900):
        await ctx.send(reply[i : i + 1900])


@bot.command(name="forget")
async def forget_cmd(ctx):
    """Clear your AI conversation memory."""
    try:
        from ai import clear_memory
        clear_memory(ctx.author.id)
        await ctx.send("🧠 Memory cleared — fresh vibes.")
    except Exception as e:
        await ctx.send(f"⚠️ {e}")


@bot.tree.command(name="ask", description="Ask Vibezzzzz AI anything ✨")
@app_commands.describe(question="What do you want to ask?")
async def slash_ask(interaction: discord.Interaction, question: str):
    await defer_thinking(interaction)
    try:
        from ai import chat
        reply = await chat(DB_PATH, interaction.user.id, question)
    except Exception as e:
        reply = f"⚠️ AI error: {e}"
    if not reply:
        reply = "…"
    if len(reply) > 1900:
        reply = reply[:1900] + "…"
    try:
        await interaction.followup.send(reply)
    except Exception as e:
        print(f"ask followup failed: {e}")


@bot.tree.command(name="forget", description="Clear your AI conversation memory")
async def slash_forget(interaction: discord.Interaction):
    await defer_thinking(interaction, ephemeral=True)
    try:
        from ai import clear_memory
        clear_memory(interaction.user.id)
        await interaction.followup.send("🧠 Memory cleared — fresh vibes.")
    except Exception as e:
        await interaction.followup.send(f"⚠️ {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    content = message.content or ""
    if content.startswith(PREFIX) or content.startswith(("v.", "vibez ", "vibe ")):
        return
    if bot.user and content.startswith(f"<@{bot.user.id}>"):
        return

    try:
        from ai import should_auto_reply, chat
    except ImportError:
        return

    if not should_auto_reply(content):
        return

    cid = new_cid()
    cmd_log.info(
        "AI-AUTO ▶ trigger matched",
        extra=ctx_extra(cid=cid, user=message.author, guild=message.guild,
                        command="ai-auto", channel=message.channel),
    )
    t0 = time.perf_counter()
    try:
        async with message.channel.typing():
            reply_text = await chat(DB_PATH, message.author.id, content)
        ms = (time.perf_counter() - t0) * 1000
        cmd_log.info(
            "AI-AUTO ✓ reply_len=%d",
            len(reply_text or ""),
            extra=ctx_extra(cid=cid, user=message.author, guild=message.guild,
                            command="ai-auto", latency_ms=ms),
        )
        for i in range(0, len(reply_text), 1900):
            await message.reply(reply_text[i : i + 1900], mention_author=False)
    except Exception as e:
        log_exception(cmd_log, "AI-AUTO failed", e, cid=cid, user=message.author)






# ─── Server logging (Circle-style) ───────────────────────────────────────────

def _can_manage_server(member: discord.Member) -> bool:
    return is_bot_owner(member.id) or (
        isinstance(member, discord.Member) and member.guild_permissions.manage_guild
    )


@bot.command(name="setlog", aliases=["logchannel", "setlogs"])
@commands.has_permissions(manage_guild=True)
async def setlog_cmd(ctx, channel: discord.TextChannel = None):
    """Set the channel for server event logs (Circle-style)."""
    channel = channel or ctx.channel
    if not ctx.guild:
        return await ctx.send("Server only.")
    from server_logs import set_log_channel
    await set_log_channel(DB_PATH, ctx.guild.id, channel.id)
    embed = discord.Embed(
        title="📋 Server Logging Enabled",
        description=f"Events will be posted in {channel.mention}\n\n"
        f"Logged: messages · members · moderation · server · voice",
        color=discord.Color.green(),
    )
    await ctx.send(embed=embed)
    # test message
    try:
        await channel.send(embed=discord.Embed(
            title="✅ Logging connected",
            description=f"Vibezzzzz will log server events here. Set by {ctx.author.mention}",
            color=discord.Color.purple(),
        ))
    except discord.Forbidden:
        await ctx.send("⚠️ I can't send messages in that channel — check my permissions.")


@setlog_cmd.error
async def setlog_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Manage Server** to set the log channel.")


@bot.command(name="unsetlog", aliases=["nolog", "disablelogs"])
@commands.has_permissions(manage_guild=True)
async def unsetlog_cmd(ctx):
    if not ctx.guild:
        return await ctx.send("Server only.")
    from server_logs import set_log_channel
    await set_log_channel(DB_PATH, ctx.guild.id, None)
    await ctx.send("📋 Server logging **disabled** for this server.")


@bot.command(name="logstatus")
async def logstatus_cmd(ctx):
    if not ctx.guild:
        return await ctx.send("Server only.")
    from server_logs import get_log_config
    cfg = await get_log_config(DB_PATH, ctx.guild.id)
    ch_id = cfg.get("log_channel_id")
    ch = ctx.guild.get_channel(ch_id) if ch_id else None
    embed = discord.Embed(title="📋 Logging Status", color=discord.Color.purple())
    embed.add_field(name="Channel", value=ch.mention if ch else ("`none`" if not ch_id else f"`{ch_id}` (missing)"), inline=False)
    for key, label in (
        ("log_messages", "Messages (edit/delete)"),
        ("log_members", "Members (join/leave/nick/roles)"),
        ("log_moderation", "Moderation (ban/unban)"),
        ("log_server", "Server (channels/roles/updates)"),
        ("log_voice", "Voice"),
    ):
        embed.add_field(name=label, value="✅" if cfg.get(key, True) else "❌", inline=True)
    await ctx.send(embed=embed)


@bot.tree.command(name="setlog", description="Set the channel for server event logs")
@app_commands.describe(channel="Channel to post logs in")
@app_commands.default_permissions(manage_guild=True)
async def slash_setlog(interaction: discord.Interaction, channel: discord.TextChannel):
    await defer_thinking(interaction)
    if not interaction.guild:
        return await interaction.followup.send("Server only.", ephemeral=True)
    from server_logs import set_log_channel
    await set_log_channel(DB_PATH, interaction.guild.id, channel.id)
    await interaction.followup.send(
        embed=discord.Embed(
            title="📋 Server Logging Enabled",
            description=f"Events → {channel.mention}",
            color=discord.Color.green(),
        )
    )
    try:
        await channel.send(embed=discord.Embed(
            title="✅ Logging connected",
            description=f"Set by {interaction.user.mention}",
            color=discord.Color.purple(),
        ))
    except discord.Forbidden:
        await interaction.followup.send("⚠️ Can't send in that channel — check permissions.", ephemeral=True)


@bot.tree.command(name="unsetlog", description="Disable server event logging")
@app_commands.default_permissions(manage_guild=True)
async def slash_unsetlog(interaction: discord.Interaction):
    await defer_thinking(interaction)
    if not interaction.guild:
        return await interaction.followup.send("Server only.", ephemeral=True)
    from server_logs import set_log_channel
    await set_log_channel(DB_PATH, interaction.guild.id, None)
    await interaction.followup.send("📋 Server logging **disabled**.")


@bot.tree.command(name="logstatus", description="Show server logging status")
async def slash_logstatus(interaction: discord.Interaction):
    await defer_thinking(interaction)
    if not interaction.guild:
        return await interaction.followup.send("Server only.", ephemeral=True)
    from server_logs import get_log_config
    cfg = await get_log_config(DB_PATH, interaction.guild.id)
    ch_id = cfg.get("log_channel_id")
    ch = interaction.guild.get_channel(ch_id) if ch_id else None
    embed = discord.Embed(title="📋 Logging Status", color=discord.Color.purple())
    embed.add_field(name="Channel", value=ch.mention if ch else "`none`", inline=False)
    for key, label in (
        ("log_messages", "Messages"),
        ("log_members", "Members"),
        ("log_moderation", "Moderation"),
        ("log_server", "Server"),
        ("log_voice", "Voice"),
    ):
        embed.add_field(name=label, value="✅" if cfg.get(key, True) else "❌", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    orig = getattr(error, "original", error)
    cid = getattr(interaction, "_log_cid", new_cid())
    cmd = interaction.command.qualified_name if interaction.command else "?"
    try:
        log_exception(
            cmd_log, f"SLASH error /{cmd}", orig,
            cid=cid, user=interaction.user, guild=interaction.guild, command=f"/{cmd}",
        )
    except Exception:
        print(f"SLASH error /{cmd}: {type(orig).__name__}: {orig}")

    detail = f"{type(orig).__name__}: {orig}"
    if len(detail) > 300:
        detail = detail[:300] + "…"
    msg = f"⚠️ **Command failed:** `{detail}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e2:
        try:
            log.warning("Could not send error reply cid=%s: %s", cid, e2)
        except Exception:
            pass


if __name__ == "__main__":
    if not TOKEN:
        log.critical("DISCORD_TOKEN not set — refusing to start")
    else:
        log.info("Starting bot (prefix=%s, owners=%d)", PREFIX, len(OWNER_IDS))
        try:
            bot.run(TOKEN)
        except Exception as e:
            log_exception(log, "Bot crashed", e)
            raise
