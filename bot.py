import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import random
import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "v!")
DASHBOARD_PORT = int(os.getenv("SERVER_PORT") or os.getenv("DASHBOARD_PORT") or "8080")
OWNER_IDS = {int(x.strip()) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}

DB_PATH = str(Path(__file__).resolve().parent / "vibezzzzz.db")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX, "v.", "vibez ", "vibe "),
    intents=intents,
    help_command=None,
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
    if is_bot_owner(member.id):
        return True
    if isinstance(member, discord.Member) and member.guild_permissions.administrator:
        return True
    return False


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
        import uvicorn
        from web import app as web_app, set_bot

        set_bot(bot)
        config = uvicorn.Config(
            web_app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning", access_log=False
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = False
        print(f"🌐 Dashboard starting on 0.0.0.0:{DASHBOARD_PORT}")
        await server.serve()
    except SystemExit:
        print(f"⚠️ Dashboard could not bind port {DASHBOARD_PORT} — bot still online.")
    except OSError as e:
        print(f"⚠️ Dashboard bind error: {e}")
    except ImportError as e:
        print(f"⚠️ Dashboard packages missing ({e}).")
    except Exception as e:
        print(f"⚠️ Dashboard error: {type(e).__name__}: {e}")


@bot.event
async def on_ready():
    await init_db()
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.playing, name="with vibes ✨ | v!help"),
    )
    print(f"✨ {bot.user} is online and ready to vibe! ✨")
    if OWNER_IDS:
        print(f"👑 Bot owners: {OWNER_IDS}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync: {e}")
    if not getattr(bot, "_dashboard_started", False):
        bot._dashboard_started = True
        asyncio.create_task(start_dashboard())


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
        return await ctx.send("❌ You need **Administrator** or be listed in `BOT_OWNER_IDS`.")
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
    embed = discord.Embed(title="✨ Vibezzzzz Commands", description=f"Prefix `{PREFIX}` + slash `/`", color=discord.Color.purple())
    embed.add_field(name="💰 Economy", value="`balance` `daily` `leaderboard` `dashboard`", inline=False)
    embed.add_field(name="🎰 Games", value="`slots <amt>` `guess <1-10> <amt>` `trivia`", inline=False)
    embed.add_field(name="🛡️ Admin / Owner", value="`addmoney @user <amt>`", inline=False)
    await ctx.send(embed=embed)


# ─── Slash commands (defer first → shows "thinking…", avoids 3s timeout) ─────

@bot.tree.command(name="balance", description="Check vibe balance ✨")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
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
    await interaction.response.defer()
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
    await interaction.response.defer()
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
    await interaction.response.defer()
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
    await interaction.response.defer()
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
    await interaction.response.defer()
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
    await interaction.response.defer(ephemeral=True)
    public = os.getenv("DASHBOARD_PUBLIC_URL", f"http://YOUR_SERVER_IP:{DASHBOARD_PORT}")
    await interaction.followup.send(
        embed=discord.Embed(title="🌐 Dashboard", description=f"**{public}**", color=discord.Color.purple())
    )


@bot.tree.command(name="addmoney", description="[Admin/Owner] Give or remove vibes")
@app_commands.describe(member="Member", amount="Amount (+ give / - remove)")
async def slash_addmoney(interaction: discord.Interaction, member: discord.Member, amount: int):
    await interaction.response.defer()
    if not can_manage_money(interaction.user):
        return await interaction.followup.send(
            "❌ You need **Administrator** or be listed in `BOT_OWNER_IDS`.", ephemeral=True
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


@bot.tree.command(name="help", description="All commands 📖")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(title="✨ Commands", color=discord.Color.purple())
    embed.add_field(name="Economy", value="`/balance` `/daily` `/leaderboard` `/dashboard`", inline=False)
    embed.add_field(name="Games", value="`/slots` `/guess` `/trivia`", inline=False)
    embed.add_field(name="Admin / Owner", value="`/addmoney`", inline=False)
    await interaction.followup.send(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN not set!")
    else:
        bot.run(TOKEN)
