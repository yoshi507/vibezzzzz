import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import random
import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import uvicorn

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "v!")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX, "v.", "vibez ", "vibe "),
    intents=intents,
    help_command=None
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
DB_PATH = "vibezzzzz.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 100,
            last_daily TEXT, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)""")
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, last_daily, wins, losses FROM users WHERE user_id = ?", (user_id,)) as cursor:
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
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now, user_id))
        await db.commit()


async def add_win_loss(user_id: int, won: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute("UPDATE users SET wins = wins + 1 WHERE user_id = ?", (user_id,))
        else:
            await db.execute("UPDATE users SET losses = losses + 1 WHERE user_id = ?", (user_id,))
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
            if feature == "gambling":
                return bool(row[0])
            if feature == "trivia":
                return bool(row[1])
    return True


@bot.event
async def on_ready():
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT 'v!',
            gambling_enabled INTEGER DEFAULT 1, trivia_enabled INTEGER DEFAULT 1)""")
        await db.commit()
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.playing, name="with vibes ✨ | v!help")
    )
    print(f"✨ {bot.user} is online and ready to vibe! ✨")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync: {e}")
    if not getattr(bot, "_dashboard_started", False):
        bot._dashboard_started = True
        try:
            from web import app as web_app, set_bot
            set_bot(bot)
            config = uvicorn.Config(web_app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")
            server = uvicorn.Server(config)
            asyncio.create_task(server.serve())
            print(f"🌐 Dashboard running at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
        except Exception as e:
            print(f"⚠️ Dashboard failed to start: {e}")


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
    embed = discord.Embed(title="☀️ Daily Vibes Claimed!", description=f"You received **{reward}** {CURRENCY} {CURRENCY_EMOJI}", color=discord.Color.gold())
    embed.add_field(name="New Balance", value=f"{bal + reward:,} {CURRENCY}")
    await ctx.send(embed=embed)


@daily.error
async def daily_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        hours = int(error.retry_after // 3600)
        minutes = int((error.retry_after % 3600) // 60)
        await ctx.send(f"⏳ You already claimed your daily! Come back in **{hours}h {minutes}m**.")


@bot.command(name="slots")
@commands.cooldown(1, 3, commands.BucketType.user)
async def slots(ctx, amount: int):
    if not await is_feature_enabled(ctx.guild.id if ctx.guild else None, "gambling"):
        await ctx.send("🚫 Gambling is disabled on this server.")
        return
    if amount <= 0:
        await ctx.send("❌ Bet must be greater than 0!")
        return
    bal, _, _, _ = await get_user(ctx.author.id)
    if amount > bal:
        await ctx.send(f"❌ You only have **{bal}** {CURRENCY}!")
        return
    await update_balance(ctx.author.id, -amount)
    reel1 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    reel2 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    reel3 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    result = f"| {reel1} | {reel2} | {reel3} |"
    multiplier = 0
    if reel1 == reel2 == reel3:
        multiplier = 50 if reel1 == "7️⃣" else (20 if reel1 == "💎" else 10)
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
        multiplier = 2
    winnings = amount * multiplier
    embed = discord.Embed(title="🎰 Vibezzzzz Slots", description=f"**{result}**", color=discord.Color.purple())
    if multiplier > 0:
        await update_balance(ctx.author.id, winnings)
        await add_win_loss(ctx.author.id, True)
        embed.add_field(name="🎉 YOU WON!", value=f"**+{winnings:,}** {CURRENCY} (x{multiplier})")
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
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Amount must be a number!")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down! Try again in {error.retry_after:.1f}s.")


@bot.command(name="guess")
@commands.cooldown(1, 5, commands.BucketType.user)
async def guess(ctx, number: int, amount: int):
    if not await is_feature_enabled(ctx.guild.id if ctx.guild else None, "gambling"):
        await ctx.send("🚫 Gambling is disabled on this server.")
        return
    if number < 1 or number > 10:
        await ctx.send("❌ Number must be between 1 and 10!")
        return
    if amount <= 0:
        await ctx.send("❌ Bet must be greater than 0!")
        return
    bal, _, _, _ = await get_user(ctx.author.id)
    if amount > bal:
        await ctx.send(f"❌ You only have **{bal}** {CURRENCY}!")
        return
    await update_balance(ctx.author.id, -amount)
    secret = random.randint(1, 10)
    embed = discord.Embed(title="🎲 Number Guess", color=discord.Color.purple())
    embed.add_field(name="Your Guess", value=str(number), inline=True)
    embed.add_field(name="The Number", value=str(secret), inline=True)
    if number == secret:
        winnings = amount * 5
        await update_balance(ctx.author.id, winnings)
        await add_win_loss(ctx.author.id, True)
        embed.description = f"🎉 **CORRECT!** You won **{winnings:,}** {CURRENCY}!"
        embed.color = discord.Color.green()
    else:
        await add_win_loss(ctx.author.id, False)
        embed.description = f"💔 Wrong! You lost **{amount:,}** {CURRENCY}."
        embed.color = discord.Color.red()
    new_bal, _, _, _ = await get_user(ctx.author.id)
    embed.set_footer(text=f"New balance: {new_bal:,} {CURRENCY}")
    await ctx.send(embed=embed)


@guess.error
async def guess_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Usage: `{PREFIX}guess <number 1-10> <bet amount>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Both number and amount must be numbers!")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down! Try again in {error.retry_after:.1f}s.")


@bot.command(name="trivia")
@commands.cooldown(1, 10, commands.BucketType.user)
async def trivia(ctx):
    if not await is_feature_enabled(ctx.guild.id if ctx.guild else None, "trivia"):
        await ctx.send("🚫 Trivia is disabled on this server.")
        return
    q = random.choice(TRIVIA_QUESTIONS)
    embed = discord.Embed(title="🧠 Trivia Time!", description=q["q"], color=discord.Color.blue())
    embed.set_footer(text="You have 15 seconds to answer!")
    await ctx.send(embed=embed)
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel
    try:
        msg = await bot.wait_for("message", check=check, timeout=15.0)
        answer = msg.content.lower().strip()
        if answer == q["a"] or q["a"] in answer:
            reward = random.randint(25, 75)
            await update_balance(ctx.author.id, reward)
            await add_win_loss(ctx.author.id, True)
            await ctx.send(f"✅ Correct! You earned **{reward}** {CURRENCY} {CURRENCY_EMOJI}")
        else:
            await add_win_loss(ctx.author.id, False)
            await ctx.send(f"❌ Wrong! The answer was **{q['a']}**.")
    except asyncio.TimeoutError:
        await ctx.send(f"⏰ Time's up! The answer was **{q['a']}**.")


@trivia.error
async def trivia_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down! Try again in {error.retry_after:.1f}s.")


@bot.command(name="leaderboard", aliases=["lb", "top"])
async def leaderboard(ctx):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await ctx.send("No one has vibes yet!")
        return
    embed = discord.Embed(title="🏆 Top Vibers Leaderboard", color=discord.Color.gold())
    description = ""
    for i, (user_id, bal) in enumerate(rows, 1):
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
        description += f"{medal} {user.display_name if user else 'Unknown'} — **{bal:,}** {CURRENCY}\n"
    embed.description = description
    await ctx.send(embed=embed)


@bot.command(name="dashboard", aliases=["dash", "panel"])
async def dashboard_cmd(ctx):
    public = os.getenv("DASHBOARD_PUBLIC_URL", f"http://YOUR_SERVER_IP:{DASHBOARD_PORT}")
    embed = discord.Embed(
        title="🌐 Vibezzzzz Dashboard",
        description=f"Manage stats, leaderboards & your servers:\n\n**{public}**",
        color=discord.Color.purple()
    )
    embed.add_field(name="Features", value="• Global stats & leaderboard\n• Login with Discord\n• Per-server settings\n• Give vibes to members")
    embed.set_footer(text="Set DASHBOARD_PUBLIC_URL env var for a clean link")
    await ctx.send(embed=embed)


@bot.command(name="addmoney", aliases=["givemoney", "addvibes"])
@commands.has_permissions(administrator=True)
async def addmoney(ctx, member: discord.Member, amount: int):
    if amount == 0:
        await ctx.send("❌ Amount can't be 0!")
        return
    await get_user(member.id)
    await update_balance(member.id, amount)
    new_bal, _, _, _ = await get_user(member.id)
    action = "gave" if amount > 0 else "removed"
    embed = discord.Embed(
        title="💰 Admin Money Update",
        description=f"{ctx.author.mention} {action} **{abs(amount):,}** {CURRENCY} {'to' if amount > 0 else 'from'} {member.mention}",
        color=discord.Color.green() if amount > 0 else discord.Color.orange()
    )
    embed.add_field(name="New Balance", value=f"**{new_bal:,}** {CURRENCY}")
    await ctx.send(embed=embed)


@addmoney.error
async def addmoney_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Administrator** permission to use this.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Usage: `{PREFIX}addmoney @user <amount>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Please mention a valid member and a number amount.")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="✨ Vibezzzzz Bot Commands",
        description=f"Prefix: `{PREFIX}`  •  Also supports **slash commands** (`/`)\nEarn and gamble with **{CURRENCY}** {CURRENCY_EMOJI}",
        color=discord.Color.purple()
    )
    embed.add_field(name="💰 Economy", value=f"`{PREFIX}balance` / `/balance` — Check vibes\n`{PREFIX}daily` / `/daily` — Daily reward\n`{PREFIX}leaderboard` / `/leaderboard` — Top players\n`{PREFIX}dashboard` / `/dashboard` — Web dashboard", inline=False)
    embed.add_field(name="🎰 Games", value=f"`{PREFIX}slots <amt>` / `/slots` — Slots\n`{PREFIX}guess <1-10> <amt>` / `/guess` — Guess (5x)\n`{PREFIX}trivia` / `/trivia` — Trivia", inline=False)
    embed.add_field(name="🛡️ Admin", value=f"`{PREFIX}addmoney @user <amt>` / `/addmoney` — Give/remove vibes", inline=False)
    embed.set_footer(text="Good luck & keep vibing! ✨")
    await ctx.send(embed=embed)


# ===== SLASH COMMANDS =====
@bot.tree.command(name="balance", description="Check your vibe balance ✨")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    bal, _, wins, losses = await get_user(member.id)
    embed = discord.Embed(title=f"{CURRENCY_EMOJI} {member.display_name}'s Vibes", color=discord.Color.purple())
    embed.add_field(name="Balance", value=f"**{bal:,}** {CURRENCY}", inline=True)
    embed.add_field(name="Wins", value=f"**{wins}**", inline=True)
    embed.add_field(name="Losses", value=f"**{losses}**", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="slots", description="Spin the slots! 🎰")
@app_commands.describe(amount="How many vibes to bet")
async def slash_slots(interaction: discord.Interaction, amount: int):
    if not await is_feature_enabled(interaction.guild_id, "gambling"):
        await interaction.response.send_message("🚫 Gambling is disabled on this server.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Bet must be greater than 0!", ephemeral=True)
        return
    bal, _, _, _ = await get_user(interaction.user.id)
    if amount > bal:
        await interaction.response.send_message(f"❌ You only have **{bal}** {CURRENCY}!", ephemeral=True)
        return
    await update_balance(interaction.user.id, -amount)
    reel1 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    reel2 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    reel3 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    result = f"| {reel1} | {reel2} | {reel3} |"
    multiplier = 0
    if reel1 == reel2 == reel3:
        multiplier = 50 if reel1 == "7️⃣" else (20 if reel1 == "💎" else 10)
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
        multiplier = 2
    winnings = amount * multiplier
    embed = discord.Embed(title="🎰 Vibezzzzz Slots", description=f"**{result}**", color=discord.Color.purple())
    if multiplier > 0:
        await update_balance(interaction.user.id, winnings)
        await add_win_loss(interaction.user.id, True)
        embed.add_field(name="🎉 YOU WON!", value=f"**+{winnings:,}** {CURRENCY} (x{multiplier})")
        embed.color = discord.Color.green()
    else:
        await add_win_loss(interaction.user.id, False)
        embed.add_field(name="💔 You lost...", value=f"-{amount:,} {CURRENCY}")
        embed.color = discord.Color.red()
    new_bal, _, _, _ = await get_user(interaction.user.id)
    embed.set_footer(text=f"New balance: {new_bal:,} {CURRENCY}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="daily", description="Claim your daily vibes! ☀️")
async def slash_daily(interaction: discord.Interaction):
    bal, last_daily, _, _ = await get_user(interaction.user.id)
    if last_daily:
        last = datetime.fromisoformat(last_daily)
        if datetime.utcnow() - last < timedelta(hours=24):
            remaining = timedelta(hours=24) - (datetime.utcnow() - last)
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            await interaction.response.send_message(f"⏳ You already claimed! Come back in **{hours}h {minutes}m**.", ephemeral=True)
            return
    reward = random.randint(50, 150)
    await update_balance(interaction.user.id, reward)
    await set_daily(interaction.user.id)
    embed = discord.Embed(title="☀️ Daily Vibes Claimed!", description=f"You received **{reward}** {CURRENCY} {CURRENCY_EMOJI}", color=discord.Color.gold())
    embed.add_field(name="New Balance", value=f"{bal + reward:,} {CURRENCY}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="guess", description="Guess a number between 1-10! 🎲")
@app_commands.describe(number="Your guess (1-10)", amount="How many vibes to bet")
async def slash_guess(interaction: discord.Interaction, number: int, amount: int):
    if not await is_feature_enabled(interaction.guild_id, "gambling"):
        await interaction.response.send_message("🚫 Gambling is disabled on this server.", ephemeral=True)
        return
    if number < 1 or number > 10:
        await interaction.response.send_message("❌ Number must be between 1 and 10!", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Bet must be greater than 0!", ephemeral=True)
        return
    bal, _, _, _ = await get_user(interaction.user.id)
    if amount > bal:
        await interaction.response.send_message(f"❌ You only have **{bal}** {CURRENCY}!", ephemeral=True)
        return
    await update_balance(interaction.user.id, -amount)
    secret = random.randint(1, 10)
    embed = discord.Embed(title="🎲 Number Guess", color=discord.Color.purple())
    embed.add_field(name="Your Guess", value=str(number), inline=True)
    embed.add_field(name="The Number", value=str(secret), inline=True)
    if number == secret:
        winnings = amount * 5
        await update_balance(interaction.user.id, winnings)
        await add_win_loss(interaction.user.id, True)
        embed.description = f"🎉 **CORRECT!** You won **{winnings:,}** {CURRENCY}!"
        embed.color = discord.Color.green()
    else:
        await add_win_loss(interaction.user.id, False)
        embed.description = f"💔 Wrong! You lost **{amount:,}** {CURRENCY}."
        embed.color = discord.Color.red()
    new_bal, _, _, _ = await get_user(interaction.user.id)
    embed.set_footer(text=f"New balance: {new_bal:,} {CURRENCY}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="trivia", description="Answer a trivia question for free vibes! 🧠")
async def slash_trivia(interaction: discord.Interaction):
    if not await is_feature_enabled(interaction.guild_id, "trivia"):
        await interaction.response.send_message("🚫 Trivia is disabled on this server.", ephemeral=True)
        return
    q = random.choice(TRIVIA_QUESTIONS)
    embed = discord.Embed(title="🧠 Trivia Time!", description=q["q"], color=discord.Color.blue())
    embed.set_footer(text="You have 15 seconds to answer!")
    await interaction.response.send_message(embed=embed)
    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel
    try:
        msg = await bot.wait_for("message", check=check, timeout=15.0)
        answer = msg.content.lower().strip()
        if answer == q["a"] or q["a"] in answer:
            reward = random.randint(25, 75)
            await update_balance(interaction.user.id, reward)
            await add_win_loss(interaction.user.id, True)
            await interaction.followup.send(f"✅ Correct! You earned **{reward}** {CURRENCY} {CURRENCY_EMOJI}")
        else:
            await add_win_loss(interaction.user.id, False)
            await interaction.followup.send(f"❌ Wrong! The answer was **{q['a']}**.")
    except asyncio.TimeoutError:
        await interaction.followup.send(f"⏰ Time's up! The answer was **{q['a']}**.")


@bot.tree.command(name="leaderboard", description="See the richest vibers 🏆")
async def slash_leaderboard(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await interaction.response.send_message("No one has vibes yet!")
        return
    embed = discord.Embed(title="🏆 Top Vibers Leaderboard", color=discord.Color.gold())
    description = ""
    for i, (user_id, bal) in enumerate(rows, 1):
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
        description += f"{medal} {user.display_name if user else 'Unknown'} — **{bal:,}** {CURRENCY}\n"
    embed.description = description
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dashboard", description="Get the web dashboard link 🌐")
async def slash_dashboard(interaction: discord.Interaction):
    public = os.getenv("DASHBOARD_PUBLIC_URL", f"http://YOUR_SERVER_IP:{DASHBOARD_PORT}")
    embed = discord.Embed(
        title="🌐 Vibezzzzz Dashboard",
        description=f"Manage stats, leaderboards & your servers:\n\n**{public}**",
        color=discord.Color.purple()
    )
    embed.add_field(name="Features", value="• Global stats & leaderboard\n• Login with Discord\n• Per-server settings\n• Give vibes to members")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="addmoney", description="[Admin] Give or remove vibes from a member")
@app_commands.describe(member="Who to give vibes to", amount="Amount (+ give / - remove)")
@app_commands.default_permissions(administrator=True)
async def slash_addmoney(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount == 0:
        await interaction.response.send_message("❌ Amount can't be 0!", ephemeral=True)
        return
    await get_user(member.id)
    await update_balance(member.id, amount)
    new_bal, _, _, _ = await get_user(member.id)
    action = "gave" if amount > 0 else "removed"
    embed = discord.Embed(
        title="💰 Admin Money Update",
        description=f"{interaction.user.mention} {action} **{abs(amount):,}** {CURRENCY} {'to' if amount > 0 else 'from'} {member.mention}",
        color=discord.Color.green() if amount > 0 else discord.Color.orange()
    )
    embed.add_field(name="New Balance", value=f"**{new_bal:,}** {CURRENCY}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Show all commands 📖")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ Vibezzzzz Bot Commands",
        description=f"Prefix: `{PREFIX}`  •  Also supports **slash commands** (`/`)\nEarn and gamble with **{CURRENCY}** {CURRENCY_EMOJI}",
        color=discord.Color.purple()
    )
    embed.add_field(name="💰 Economy", value=f"`{PREFIX}balance` / `/balance` — Check vibes\n`{PREFIX}daily` / `/daily` — Daily reward\n`{PREFIX}leaderboard` / `/leaderboard` — Top players\n`{PREFIX}dashboard` / `/dashboard` — Web dashboard", inline=False)
    embed.add_field(name="🎰 Games", value=f"`{PREFIX}slots <amt>` / `/slots` — Slo