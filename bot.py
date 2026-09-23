import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import random
import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "v!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# Currency name
CURRENCY = "vibes"
CURRENCY_EMOJI = "✨"

# Slot symbols
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
SLOT_WEIGHTS = [30, 25, 20, 15, 8, 2]  # Higher = more common

# Trivia questions
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 100,
                last_daily TEXT,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0
            )
        """)
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


@bot.event
async def on_ready():
    await init_db()
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="with vibes ✨ | v!help"
        )
    )
    print(f"✨ {bot.user} is online and ready to vibe! ✨")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync: {e}")


@bot.command(name="balance", aliases=["bal", "vibes"])
async def balance(ctx, member: discord.Member = None):
    """Check your or someone else's vibe balance ✨"""
    member = member or ctx.author
    bal, _, wins, losses = await get_user(member.id)
    embed = discord.Embed(
        title=f"{CURRENCY_EMOJI} {member.display_name}'s Vibes",
        color=discord.Color.purple()
    )
    embed.add_field(name="Balance", value=f"**{bal:,}** {CURRENCY}", inline=True)
    embed.add_field(name="Wins", value=f"**{wins}**", inline=True)
    embed.add_field(name="Losses", value=f"**{losses}**", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="daily")
@commands.cooldown(1, 86400, commands.BucketType.user)
async def daily(ctx):
    """Claim your daily vibes! ☀️"""
    bal, last_daily, _, _ = await get_user(ctx.author.id)
    
    reward = random.randint(50, 150)
    await update_balance(ctx.author.id, reward)
    await set_daily(ctx.author.id)
    
    embed = discord.Embed(
        title="☀️ Daily Vibes Claimed!",
        description=f"You received **{reward}** {CURRENCY} {CURRENCY_EMOJI}",
        color=discord.Color.gold()
    )
    new_bal = bal + reward
    embed.add_field(name="New Balance", value=f"{new_bal:,} {CURRENCY}")
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
    """Spin the slots! Bet some vibes 🎰
    Usage: v!slots <amount>
    """
    if amount <= 0:
        await ctx.send("❌ Bet must be greater than 0!")
        return
    
    bal, _, _, _ = await get_user(ctx.author.id)
    if amount > bal:
        await ctx.send(f"❌ You only have **{bal}** {CURRENCY}!")
        return
    
    # Deduct bet
    await update_balance(ctx.author.id, -amount)
    
    # Spin
    reel1 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    reel2 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    reel3 = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]
    
    result = f"| {reel1} | {reel2} | {reel3} |"
    
    # Calculate winnings
    multiplier = 0
    if reel1 == reel2 == reel3:
        if reel1 == "7️⃣":
            multiplier = 50  # Jackpot
        elif reel1 == "💎":
            multiplier = 20
        else:
            multiplier = 10
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
        multiplier = 2
    
    winnings = amount * multiplier
    
    embed = discord.Embed(
        title="🎰 Vibezzzzz Slots",
        description=f"**{result}**",
        color=discord.Color.purple()
    )
    
    if multiplier > 0:
        await update_balance(ctx.author.id, winnings)
        await add_win_loss(ctx.author.id, True)
        embed.add_field(name="🎉 YOU WON!", value=f"**+{winnings:,}** {CURRENCY} (x{multiplier})", inline=False)
        embed.color = discord.Color.green()
    else:
        await add_win_loss(ctx.author.id, False)
        embed.add_field(name="💔 You lost...", value=f"-{amount:,} {CURRENCY}", inline=False)
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
    """Guess a number between 1-10! 🎲
    Usage: v!guess <1-10> <bet>
    Correct guess = 5x your bet!
    """
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
    
    embed = discord.Embed(
        title="🎲 Number Guess",
        color=discord.Color.purple()
    )
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
    """Answer a trivia question for free vibes! 🧠
    Correct answer = 25-75 vibes
    """
    q = random.choice(TRIVIA_QUESTIONS)
    
    embed = discord.Embed(
        title="🧠 Trivia Time!",
        description=q["q"],
        color=discord.Color.blue()
    )
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
    """See the richest vibers 🏆"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        await ctx.send("No one has vibes yet!")
        return
    
    embed = discord.Embed(
        title="🏆 Top Vibers Leaderboard",
        color=discord.Color.gold()
    )
    
    description = ""
    for i, (user_id, bal) in enumerate(rows, 1):
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
        description += f"{medal} {user.display_name if user else 'Unknown'} — **{bal:,}** {CURRENCY}\n"
    
    embed.description = description
    await ctx.send(embed=embed)


@bot.command(name="help")
async def help_command(ctx):
    """Show all commands 📖"""
    embed = discord.Embed(
        title="✨ Vibezzzzz Bot Commands",
        description=f"Prefix: `{PREFIX}`  •  Also supports **slash commands** (`/`)\nEarn and gamble with **{CURRENCY}** {CURRENCY_EMOJI}",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="💰 Economy",
        value=f"`{PREFIX}balance` / `/balance` — Check your vibes\n"
              f"`{PREFIX}daily` / `/daily` — Claim daily reward\n"
              f"`{PREFIX}leaderboard` / `/leaderboard` — Top players",
        inline=False
    )
    embed.add_field(
        name="🎰 Games",
        value=f"`{PREFIX}slots <amount>` / `/slots` — Spin the slots!\n"
              f"`{PREFIX}guess <1-10> <amount>` / `/guess` — Guess the number (5x payout)\n"
              f"`{PREFIX}trivia` / `/trivia` — Answer trivia for free vibes",
        inline=False
    )
    embed.set_footer(text="Good luck & keep vibing! ✨")
    await ctx.send(embed=embed)


# Slash commands
@bot.tree.command(name="balance", description="Check your vibe balance ✨")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    bal, _, wins, losses = await get_user(member.id)
    embed = discord.Embed(
        title=f"{CURRENCY_EMOJI} {member.display_name}'s Vibes",
        color=discord.Color.purple()
    )
    embed.add_field(name="Balance", value=f"**{bal:,}** {CURRENCY}", inline=True)
    embed.add_field(name="Wins", value=f"**{wins}**", inline=True)
    embed.add_field(name="Losses", value=f"**{losses}**", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="slots", description="Spin the slots! 🎰")
@app_commands.describe(amount="How many vibes to bet")
async def slash_slots(interaction: discord.Interaction, amount: int):
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
        if reel1 == "7️⃣":
            multiplier = 50
        elif reel1 == "💎":
            multiplier = 20
        else:
            multiplier = 10
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
        multiplier = 2
    
    winnings = amount * multiplier
    
    embed = discord.Embed(
        title="🎰 Vibezzzzz Slots",
        description=f"**{result}**",
        color=discord.Color.purple()
    )
    
    if multiplier > 0:
        await update_balance(interaction.user.id, winnings)
        await add_win_loss(interaction.user.id, True)
        embed.add_field(name="🎉 YOU WON!", value=f"**+{winnings:,}** {CURRENCY} (x{multiplier})", inline=False)
        embed.color = discord.Color.green()
    else:
        await add_win_loss(interaction.user.id, False)
        embed.add_field(name="💔 You lost...", value=f"-{amount:,} {CURRENCY}", inline=False)
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
    
    embed = discord.Embed(
        title="☀️ Daily Vibes Claimed!",
        description=f"You received **{reward}** {CURRENCY} {CURRENCY_EMOJI}",
        color=discord.Color.gold()
    )
    new_bal = bal + reward
    embed.add_field(name="New Balance", value=f"{new_bal:,} {CURRENCY}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="guess", description="Guess a number between 1-10! 🎲")
@app_commands.describe(number="Your guess (1-10)", amount="How many vibes to bet")
async def slash_guess(interaction: discord.Interaction, number: int, amount: int):
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
    q = random.choice(TRIVIA_QUESTIONS)

    embed = discord.Embed(
        title="🧠 Trivia Time!",
        description=q["q"],
        color=discord.Color.blue()
    )
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

    embed = discord.Embed(
        title="🏆 Top Vibers Leaderboard",
        color=discord.Color.gold()
    )

    description = ""
    for i, (user_id, bal) in enumerate(rows, 1):
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
        description += f"{medal} {user.display_name if user else 'Unknown'} — **{bal:,}** {CURRENCY}\n"
    embed.description = description
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Show all commands 📖")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ Vibezzzzz Bot Commands",
        description=f"Prefix: `{PREFIX}`  •  Also supports **slash commands** (`/`)\nEarn and gamble with **{CURRENCY}** {CURRENCY_EMOJI}",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="💰 Economy",
        value=f"`{PREFIX}balance` / `/balance` — Check your vibes\n"
              f"`{PREFIX}daily` / `/daily` — Claim daily reward\n"
              f"`{PREFIX}leaderboard` / `/leaderboard` — Top players",
        inline=False
    )
    embed.add_field(
        name="🎰 Games",
        value=f"`{PREFIX}slots <amount>` / `/slots` — Spin the slots!\n"
              f"`{PREFIX}guess <1-10> <amount>` / `/guess` — Guess the number (5x payout)\n"
              f"`{PREFIX}trivia` / `/trivia` — Answer trivia for free vibes",
        inline=False
    )
    embed.set_footer(text="Good luck & keep vibing! ✨")
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN not set! Please set it in your .env or environment.")
    else:
        bot.run(TOKEN)
