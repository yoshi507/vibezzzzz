"""
Cat Bot–style collection system for Vibezzzzz.
Packs, inventory, catnip, leaderboards, simple battlepass.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands
from discord import app_commands

try:
    from logging_setup import get_logger
    log = get_logger("vibezzzzz.cats")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.cats")

# rarity weights & display
CATS = {
    "common": [
        ("Tabby", "🐱"), ("Orange", "🐈"), ("Calico", "😺"), ("Grey", "😼"),
        ("Black", "🐈‍⬛"), ("White", "🤍"), ("Siamese", "😸"),
    ],
    "uncommon": [
        ("Maine Coon", "🦁"), ("Ragdoll", "🧸"), ("British Shorthair", "😾"),
        ("Persian", "👑"), ("Bengal", "🐆"),
    ],
    "rare": [
        ("Sphynx", "👽"), ("Russian Blue", "💙"), ("Abyssinian", "✨"),
        ("Norwegian Forest", "🌲"),
    ],
    "epic": [
        ("Celestial Cat", "🌟"), ("Void Cat", "🌑"), ("Rainbow Cat", "🌈"),
    ],
    "legendary": [
        ("Cosmic Mew", "🚀"), ("Dragon Cat", "🐉"), ("Goddess Bastet", "👁️"),
    ],
}
RARITY_WEIGHTS = {"common": 55, "uncommon": 25, "rare": 12, "epic": 6, "legendary": 2}
RARITY_COLOR = {
    "common": discord.Color.light_grey(),
    "uncommon": discord.Color.green(),
    "rare": discord.Color.blue(),
    "epic": discord.Color.purple(),
    "legendary": discord.Color.gold(),
}
PACK_COST_CATNIP = 50
DAILY_CATNIP = 25
BATTLEPASS_GOAL = 10  # packs opened for free legendary claim once


async def ensure_cat_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS cat_users (
                user_id INTEGER PRIMARY KEY,
                catnip INTEGER DEFAULT 100,
                packs_opened INTEGER DEFAULT 0,
                battlepass_claimed INTEGER DEFAULT 0,
                last_daily TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS cat_inventory (
                user_id INTEGER,
                cat_name TEXT,
                rarity TEXT,
                emoji TEXT,
                count INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, cat_name)
            )"""
        )
        await db.commit()


async def get_cat_user(db_path: str, user_id: int) -> dict:
    await ensure_cat_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT catnip, packs_opened, battlepass_claimed, last_daily FROM cat_users WHERE user_id=?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO cat_users (user_id, catnip) VALUES (?, 100)", (user_id,)
            )
            await db.commit()
            return {"catnip": 100, "packs_opened": 0, "battlepass_claimed": 0, "last_daily": None}
        return {
            "catnip": row[0],
            "packs_opened": row[1],
            "battlepass_claimed": row[2],
            "last_daily": row[3],
        }


async def add_catnip(db_path: str, user_id: int, amount: int) -> int:
    u = await get_cat_user(db_path, user_id)
    new = max(0, u["catnip"] + amount)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE cat_users SET catnip=? WHERE user_id=?", (new, user_id))
        await db.commit()
    return new


async def add_cat(db_path: str, user_id: int, name: str, rarity: str, emoji: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO cat_inventory (user_id, cat_name, rarity, emoji, count) VALUES (?,?,?,?,1)
               ON CONFLICT(user_id, cat_name) DO UPDATE SET count = count + 1""",
            (user_id, name, rarity, emoji),
        )
        await db.commit()


async def inventory(db_path: str, user_id: int) -> list:
    await ensure_cat_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT cat_name, rarity, emoji, count FROM cat_inventory WHERE user_id=? ORDER BY rarity, cat_name",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


def roll_cat() -> tuple[str, str, str]:
    rarities = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    rarity = random.choices(rarities, weights=weights, k=1)[0]
    name, emoji = random.choice(CATS[rarity])
    return name, rarity, emoji


def register_cats(bot: commands.Bot, db_path: str) -> None:

    @bot.command(name="catnip", aliases=["cn"])
    async def catnip_cmd(ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        u = await get_cat_user(db_path, member.id)
        await ctx.send(f"🌿 **{member.display_name}** has **{u['catnip']}** catnip")

    @bot.command(name="dailycat", aliases=["catdaily"])
    async def dailycat_cmd(ctx: commands.Context):
        u = await get_cat_user(db_path, ctx.author.id)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if u["last_daily"] == today:
            return await ctx.send("⏳ You already claimed daily catnip today.")
        new = await add_catnip(db_path, ctx.author.id, DAILY_CATNIP)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE cat_users SET last_daily=? WHERE user_id=?", (today, ctx.author.id)
            )
            await db.commit()
        await ctx.send(f"🌿 Daily **+{DAILY_CATNIP}** catnip! Balance: **{new}**")

    @bot.command(name="pack", aliases=["packs", "opencat"])
    async def pack_cmd(ctx: commands.Context):
        u = await get_cat_user(db_path, ctx.author.id)
        if u["catnip"] < PACK_COST_CATNIP:
            return await ctx.send(f"❌ Need **{PACK_COST_CATNIP}** catnip (you have {u['catnip']}). Try `v!dailycat`.")
        await add_catnip(db_path, ctx.author.id, -PACK_COST_CATNIP)
        name, rarity, emoji = roll_cat()
        await add_cat(db_path, ctx.author.id, name, rarity, emoji)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE cat_users SET packs_opened = packs_opened + 1 WHERE user_id=?",
                (ctx.author.id,),
            )
            await db.commit()
        embed = discord.Embed(
            title=f"{emoji} You got **{name}**!",
            description=f"Rarity: **{rarity.upper()}**\nCost: {PACK_COST_CATNIP} catnip",
            color=RARITY_COLOR.get(rarity, discord.Color.default()),
        )
        await ctx.send(embed=embed)

    @bot.command(name="inventory", aliases=["inv", "cats"])
    async def inventory_cmd(ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        items = await inventory(db_path, member.id)
        u = await get_cat_user(db_path, member.id)
        if not items:
            return await ctx.send(f"{member.display_name} has no cats yet. Open a `v!pack`!")
        lines = [f"{emoji} **{name}** ({rarity}) ×{count}" for name, rarity, emoji, count in items]
        total = sum(c for *_, c in items)
        embed = discord.Embed(
            title=f"🐈 {member.display_name}'s cats",
            description="\n".join(lines[:30]),
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"{total} cats · {u['catnip']} catnip · {u['packs_opened']} packs opened")
        await ctx.send(embed=embed)

    @bot.command(name="catlb", aliases=["catleaderboard", "catleaderboards"])
    async def catlb_cmd(ctx: commands.Context):
        await ensure_cat_tables(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT user_id, SUM(count) as total FROM cat_inventory
                   GROUP BY user_id ORDER BY total DESC LIMIT 10"""
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return await ctx.send("No cats collected yet.")
        lines = []
        for i, (uid, total) in enumerate(rows, 1):
            m = ctx.guild.get_member(uid) if ctx.guild else None
            name = m.display_name if m else str(uid)
            lines.append(f"**{i}.** {name} — **{total}** cats")
        await ctx.send(embed=discord.Embed(title="🏆 Cat leaderboard", description="\n".join(lines), color=discord.Color.gold()))

    @bot.command(name="battlepass", aliases=["bp"])
    async def battlepass_cmd(ctx: commands.Context):
        u = await get_cat_user(db_path, ctx.author.id)
        progress = u["packs_opened"]
        claimed = u["battlepass_claimed"]
        embed = discord.Embed(
            title="🎫 Cat Battlepass",
            description=(
                f"Open **{BATTLEPASS_GOAL}** packs to claim a free **Legendary** cat.\n\n"
                f"Progress: **{min(progress, BATTLEPASS_GOAL)}/{BATTLEPASS_GOAL}** packs\n"
                f"Claimed: {'Yes ✅' if claimed else 'No'}"
            ),
            color=discord.Color.purple(),
        )
        if progress >= BATTLEPASS_GOAL and not claimed:
            name, rarity, emoji = random.choice(CATS["legendary"])[0], "legendary", None
            # pick properly
            name, emoji = random.choice(CATS["legendary"])
            await add_cat(db_path, ctx.author.id, name, "legendary", emoji)
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE cat_users SET battlepass_claimed=1 WHERE user_id=?", (ctx.author.id,)
                )
                await db.commit()
            embed.description += f"\n\n🎁 Claimed **{emoji} {name}**!"
        await ctx.send(embed=embed)

    # slash
    @bot.tree.command(name="catnip", description="Check catnip balance")
    async def slash_catnip(interaction: discord.Interaction, member: discord.Member = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        member = member or interaction.user
        u = await get_cat_user(db_path, member.id)
        await interaction.followup.send(f"🌿 **{member.display_name}** has **{u['catnip']}** catnip")

    @bot.tree.command(name="pack", description=f"Open a cat pack ({PACK_COST_CATNIP} catnip)")
    async def slash_pack(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        u = await get_cat_user(db_path, interaction.user.id)
        if u["catnip"] < PACK_COST_CATNIP:
            return await interaction.followup.send(f"❌ Need **{PACK_COST_CATNIP}** catnip.")
        await add_catnip(db_path, interaction.user.id, -PACK_COST_CATNIP)
        name, rarity, emoji = roll_cat()
        await add_cat(db_path, interaction.user.id, name, rarity, emoji)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE cat_users SET packs_opened = packs_opened + 1 WHERE user_id=?",
                (interaction.user.id,),
            )
            await db.commit()
        await interaction.followup.send(embed=discord.Embed(
            title=f"{emoji} You got **{name}**!",
            description=f"Rarity: **{rarity.upper()}**",
            color=RARITY_COLOR.get(rarity),
        ))

    @bot.tree.command(name="inventory", description="View your cat collection")
    async def slash_inv(interaction: discord.Interaction, member: discord.Member = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        member = member or interaction.user
        items = await inventory(db_path, member.id)
        if not items:
            return await interaction.followup.send("No cats yet — try `/pack`!")
        lines = [f"{e} **{n}** ({r}) ×{c}" for n, r, e, c in items[:30]]
        await interaction.followup.send(embed=discord.Embed(title=f"🐈 {member.display_name}", description="\n".join(lines), color=discord.Color.purple()))

    @bot.tree.command(name="catlb", description="Cat collection leaderboard")
    async def slash_catlb(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await ensure_cat_tables(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT user_id, SUM(count) FROM cat_inventory GROUP BY user_id ORDER BY 2 DESC LIMIT 10"
            ) as cur:
                rows = await cur.fetchall()
        lines = []
        for i, (uid, total) in enumerate(rows or [], 1):
            m = interaction.guild.get_member(uid) if interaction.guild else None
            lines.append(f"**{i}.** {m.display_name if m else uid} — **{total}**")
        await interaction.followup.send(embed=discord.Embed(title="🏆 Cat leaderboard", description="\n".join(lines) or "Empty", color=discord.Color.gold()))

    @bot.tree.command(name="battlepass", description="Cat battlepass progress")
    async def slash_bp(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        u = await get_cat_user(db_path, interaction.user.id)
        desc = f"Progress: **{min(u['packs_opened'], BATTLEPASS_GOAL)}/{BATTLEPASS_GOAL}**\nClaimed: {'Yes' if u['battlepass_claimed'] else 'No'}"
        if u["packs_opened"] >= BATTLEPASS_GOAL and not u["battlepass_claimed"]:
            name, emoji = random.choice(CATS["legendary"])
            await add_cat(db_path, interaction.user.id, name, "legendary", emoji)
            async with aiosqlite.connect(db_path) as db:
                await db.execute("UPDATE cat_users SET battlepass_claimed=1 WHERE user_id=?", (interaction.user.id,))
                await db.commit()
            desc += f"\n🎁 Claimed **{emoji} {name}**!"
        await interaction.followup.send(embed=discord.Embed(title="🎫 Cat Battlepass", description=desc, color=discord.Color.purple()))

    @bot.tree.command(name="dailycat", description="Claim daily catnip")
    async def slash_dailycat(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        u = await get_cat_user(db_path, interaction.user.id)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if u["last_daily"] == today:
            return await interaction.followup.send("⏳ Already claimed today.")
        new = await add_catnip(db_path, interaction.user.id, DAILY_CATNIP)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE cat_users SET last_daily=? WHERE user_id=?", (today, interaction.user.id))
            await db.commit()
        await interaction.followup.send(f"🌿 Daily **+{DAILY_CATNIP}** catnip! Balance: **{new}**")

    log.info("Cat system registered")
