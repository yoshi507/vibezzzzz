"""
Counting, giveaways, honeypot, tickets, welcome messages.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands
from discord import app_commands, ui

try:
    from logging_setup import get_logger
    log = get_logger("vibezzzzz.extras")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.extras")


async def ensure_extra_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS guild_extra (
                guild_id INTEGER PRIMARY KEY,
                count_channel_id INTEGER,
                count_current INTEGER DEFAULT 0,
                count_last_user INTEGER,
                honeypot_channel_id INTEGER,
                welcome_channel_id INTEGER,
                welcome_message TEXT,
                ticket_category_id INTEGER,
                ticket_log_id INTEGER,
                ticket_counter INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS giveaways (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                guild_id INTEGER,
                prize TEXT,
                winners INTEGER,
                ends_at TEXT,
                ended INTEGER DEFAULT 0,
                host_id INTEGER
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS giveaway_entries (
                message_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (message_id, user_id)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS afk_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                since TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS timed_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                channel_id INTEGER,
                author_id INTEGER,
                content TEXT,
                send_at TEXT,
                sent INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS message_stats (
                guild_id INTEGER,
                user_id INTEGER,
                day TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, day)
            )"""
        )
        await db.commit()


async def get_extra(db_path: str, guild_id: int) -> dict:
    await ensure_extra_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT * FROM guild_extra WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await db.execute("INSERT INTO guild_extra (guild_id) VALUES (?)", (guild_id,))
                await db.commit()
                return {"guild_id": guild_id}
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


async def set_extra(db_path: str, guild_id: int, **kwargs) -> None:
    cfg = await get_extra(db_path, guild_id)
    cfg.update(kwargs)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO guild_extra (
                guild_id, count_channel_id, count_current, count_last_user,
                honeypot_channel_id, welcome_channel_id, welcome_message,
                ticket_category_id, ticket_log_id, ticket_counter
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
                count_channel_id=excluded.count_channel_id,
                count_current=excluded.count_current,
                count_last_user=excluded.count_last_user,
                honeypot_channel_id=excluded.honeypot_channel_id,
                welcome_channel_id=excluded.welcome_channel_id,
                welcome_message=excluded.welcome_message,
                ticket_category_id=excluded.ticket_category_id,
                ticket_log_id=excluded.ticket_log_id,
                ticket_counter=excluded.ticket_counter
            """,
            (
                guild_id,
                cfg.get("count_channel_id"),
                cfg.get("count_current") or 0,
                cfg.get("count_last_user"),
                cfg.get("honeypot_channel_id"),
                cfg.get("welcome_channel_id"),
                cfg.get("welcome_message"),
                cfg.get("ticket_category_id"),
                cfg.get("ticket_log_id"),
                cfg.get("ticket_counter") or 0,
            ),
        )
        await db.commit()


def parse_duration(text: str) -> Optional[timedelta]:
    if not text:
        return None
    total = 0
    for num, unit in re.findall(r"(\d+)([smhdw])", text.lower()):
        total += int(num) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return timedelta(seconds=total) if total > 0 else None


class GiveawayView(ui.View):
    def __init__(self, db_path: str, message_id: int = 0):
        super().__init__(timeout=None)
        self.db_path = db_path
        self.message_id = message_id

    @ui.button(label="🎉 Enter", style=discord.ButtonStyle.primary, custom_id="gw_enter")
    async def enter(self, interaction: discord.Interaction, button: ui.Button):
        mid = interaction.message.id if interaction.message else self.message_id
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT ended FROM giveaways WHERE message_id=?", (mid,)) as cur:
                row = await cur.fetchone()
            if not row:
                return await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            if row[0]:
                return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            try:
                await db.execute(
                    "INSERT INTO giveaway_entries (message_id, user_id) VALUES (?,?)",
                    (mid, interaction.user.id),
                )
                await db.commit()
                await interaction.response.send_message("✅ You're in!", ephemeral=True)
            except Exception:
                await interaction.response.send_message("You're already entered.", ephemeral=True)


class TicketView(ui.View):
    def __init__(self, db_path: str):
        super().__init__(timeout=None)
        self.db_path = db_path

    @ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.green, custom_id="ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        cfg = await get_extra(self.db_path, guild.id)
        # prevent duplicate
        for ch in guild.text_channels:
            if ch.topic and f"ticket-user:{interaction.user.id}" in (ch.topic or ""):
                return await interaction.response.send_message(f"You already have {ch.mention}", ephemeral=True)
        cat = guild.get_channel(cfg.get("ticket_category_id") or 0)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        for role in guild.roles:
            if role.permissions.manage_messages:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        n = (cfg.get("ticket_counter") or 0) + 1
        await set_extra(self.db_path, guild.id, ticket_counter=n)
        channel = await guild.create_text_channel(
            f"ticket-{n}",
            category=cat if isinstance(cat, discord.CategoryChannel) else None,
            overwrites=overwrites,
            topic=f"ticket-user:{interaction.user.id}",
            reason=f"Ticket by {interaction.user}",
        )
        await channel.send(
            content=f"{interaction.user.mention}",
            embed=discord.Embed(
                title="🎫 Ticket opened",
                description="Staff will be with you shortly.\nUse `v!close` or `/close` to close.",
                color=discord.Color.green(),
            ),
        )
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)


def register_extras(bot: commands.Bot, db_path: str) -> None:
    bot.add_view(GiveawayView(db_path))
    bot.add_view(TicketView(db_path))

    # ── Counting ─────────────────────────────────────────────────────────────
    @bot.command(name="setcount")
    @commands.has_permissions(manage_guild=True)
    async def setcount(ctx: commands.Context, channel: discord.TextChannel):
        await set_extra(db_path, ctx.guild.id, count_channel_id=channel.id, count_current=0, count_last_user=None)
        await ctx.send(f"🔢 Counting channel set to {channel.mention} — start at **1**")

    @bot.command(name="count")
    async def count_cmd(ctx: commands.Context):
        cfg = await get_extra(db_path, ctx.guild.id)
        ch = ctx.guild.get_channel(cfg.get("count_channel_id") or 0)
        await ctx.send(f"Next number: **{(cfg.get('count_current') or 0) + 1}** in {ch.mention if ch else '`not set`'}")

    # ── Honeypot ─────────────────────────────────────────────────────────────
    @bot.command(name="sethoneypot", aliases=["honeypot"])
    @commands.has_permissions(administrator=True)
    async def sethoneypot(ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await set_extra(db_path, ctx.guild.id, honeypot_channel_id=channel.id)
        await ctx.send(f"🍯 Honeypot set to {channel.mention} — anyone who chats there gets **banned**.")

    @bot.command(name="unsethoneypot")
    @commands.has_permissions(administrator=True)
    async def unsethoneypot(ctx: commands.Context):
        await set_extra(db_path, ctx.guild.id, honeypot_channel_id=None)
        await ctx.send("🍯 Honeypot disabled.")

    # ── Welcome ──────────────────────────────────────────────────────────────
    @bot.command(name="setwelcome")
    @commands.has_permissions(manage_guild=True)
    async def setwelcome(ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        await set_extra(db_path, ctx.guild.id, welcome_channel_id=channel.id, welcome_message=message)
        await ctx.send(
            f"👋 Welcome channel {channel.mention}\n"
            f"Message saved. Placeholders: `{{user}}` `{{user_mention}}` `{{server}}` `{{member_count}}`"
        )

    @bot.command(name="testwelcome")
    @commands.has_permissions(manage_guild=True)
    async def testwelcome(ctx: commands.Context):
        cfg = await get_extra(db_path, ctx.guild.id)
        msg = cfg.get("welcome_message") or "Welcome {user_mention} to **{server}**!"
        text = (
            msg.replace("{user}", ctx.author.name)
            .replace("{user_mention}", ctx.author.mention)
            .replace("{server}", ctx.guild.name)
            .replace("{member_count}", str(ctx.guild.member_count))
        )
        await ctx.send(text)

    # ── Tickets ──────────────────────────────────────────────────────────────
    @bot.command(name="ticketpanel", aliases=["tickets"])
    @commands.has_permissions(manage_guild=True)
    async def ticketpanel(ctx: commands.Context, *, title: str = "Support Tickets"):
        cfg = await get_extra(db_path, ctx.guild.id)
        if not cfg.get("ticket_category_id"):
            cat = await ctx.guild.create_category("Tickets")
            await set_extra(db_path, ctx.guild.id, ticket_category_id=cat.id)
        embed = discord.Embed(title=f"🎫 {title}", description="Click the button below to open a private ticket.", color=discord.Color.blurple())
        await ctx.send(embed=embed, view=TicketView(db_path))

    @bot.command(name="close")
    async def close_ticket(ctx: commands.Context):
        if not ctx.channel.topic or "ticket-user:" not in (ctx.channel.topic or ""):
            return await ctx.send("This isn't a ticket channel.")
        if not (
            ctx.author.guild_permissions.manage_messages
            or f"ticket-user:{ctx.author.id}" in (ctx.channel.topic or "")
        ):
            return await ctx.send("No permission.")
        await ctx.send("Closing in 3s…")
        await asyncio.sleep(3)
        await ctx.channel.delete(reason=f"Closed by {ctx.author}")

    # ── Giveaways ────────────────────────────────────────────────────────────
    @bot.command(name="gstart", aliases=["giveaway"])
    @commands.has_permissions(manage_guild=True)
    async def gstart(ctx: commands.Context, duration: str, winners: int = 1, *, prize: str):
        delta = parse_duration(duration)
        if not delta:
            return await ctx.send("Duration like `10m` `1h` `1d`")
        if winners < 1 or winners > 20:
            return await ctx.send("Winners 1–20")
        ends = datetime.now(timezone.utc) + delta
        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(ends.timestamp())}:R>\n\nClick **Enter** to join!",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Hosted by {ctx.author}")
        view = GiveawayView(db_path)
        msg = await ctx.send(embed=embed, view=view)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO giveaways (message_id, channel_id, guild_id, prize, winners, ends_at, host_id) VALUES (?,?,?,?,?,?,?)",
                (msg.id, ctx.channel.id, ctx.guild.id, prize, winners, ends.isoformat(), ctx.author.id),
            )
            await db.commit()
        view.message_id = msg.id
        asyncio.create_task(_end_giveaway_later(bot, db_path, msg.id, delta.total_seconds()))

    @bot.command(name="gend")
    @commands.has_permissions(manage_guild=True)
    async def gend(ctx: commands.Context, message_id: int):
        await _finish_giveaway(bot, db_path, message_id, ctx.channel)

    @bot.command(name="greroll")
    @commands.has_permissions(manage_guild=True)
    async def greroll(ctx: commands.Context, message_id: int):
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT winners FROM giveaways WHERE message_id=?", (message_id,)) as cur:
                row = await cur.fetchone()
            async with db.execute("SELECT user_id FROM giveaway_entries WHERE message_id=?", (message_id,)) as cur:
                entries = [r[0] for r in await cur.fetchall()]
        if not row or not entries:
            return await ctx.send("No entries / giveaway.")
        w = min(row[0], len(entries))
        winners = random.sample(entries, w)
        await ctx.send("🎉 Reroll winners: " + " ".join(f"<@{u}>" for u in winners))

    # ── Listeners ────────────────────────────────────────────────────────────
    @bot.listen("on_message")
    async def extras_on_message(message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # track message count for stats
        try:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """INSERT INTO message_stats (guild_id, user_id, day, count) VALUES (?,?,?,1)
                       ON CONFLICT(guild_id, user_id, day) DO UPDATE SET count = count + 1""",
                    (message.guild.id, message.author.id, day),
                )
                await db.commit()
        except Exception:
            pass
        cfg = await get_extra(db_path, message.guild.id)

        # AFK: clear on author message + notify on mentions
        try:
            def _is_afk_cmd(content: str) -> bool:
                c = (content or "").lower().strip()
                for p in ("v!", "v.", "!", "/", ",", ".", "vibez ", "vibe "):
                    if c.startswith(p):
                        c = c[len(p):].strip()
                        break
                first = c.split()[0] if c else ""
                return first in ("afk", "unafk", "back")

            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    "SELECT reason, since FROM afk_users WHERE user_id=?", (message.author.id,)
                ) as cur:
                    own = await cur.fetchone()
                # Do NOT clear on the same message that sets AFK (any prefix)
                if own and not _is_afk_cmd(message.content):
                    await db.execute("DELETE FROM afk_users WHERE user_id=?", (message.author.id,))
                    await db.commit()
                    try:
                        await message.channel.send(
                            f"👋 Welcome back {message.author.mention} — AFK removed.",
                            delete_after=8,
                        )
                    except Exception:
                        pass
                # mentions of AFK users
                if message.mentions:
                    for m in message.mentions[:5]:
                        if m.bot or m.id == message.author.id:
                            continue
                        async with db.execute(
                            "SELECT reason FROM afk_users WHERE user_id=?", (m.id,)
                        ) as cur:
                            row = await cur.fetchone()
                        if row:
                            try:
                                await message.channel.send(
                                    f"💤 **{m.display_name}** is AFK: {row[0]}",
                                    delete_after=15,
                                )
                            except Exception:
                                pass
        except Exception:
            pass

        # honeypot
        if cfg.get("honeypot_channel_id") and message.channel.id == cfg["honeypot_channel_id"]:
            try:
                await message.author.ban(reason="Honeypot channel", delete_message_days=0)
                await message.channel.send(f"🍯 Banned {message.author} (honeypot).", delete_after=10)
            except Exception:
                pass
            return

        # counting
        if cfg.get("count_channel_id") and message.channel.id == cfg["count_channel_id"]:
            expected = (cfg.get("count_current") or 0) + 1
            content = message.content.strip()
            if not content.isdigit():
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            num = int(content)
            if num != expected or message.author.id == cfg.get("count_last_user"):
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            await set_extra(
                db_path, message.guild.id,
                count_current=num,
                count_last_user=message.author.id,
            )
            if num % 100 == 0:
                await message.add_reaction("🎉")

    @bot.listen("on_member_join")
    async def extras_welcome(member: discord.Member):
        cfg = await get_extra(db_path, member.guild.id)
        ch_id = cfg.get("welcome_channel_id")
        msg = cfg.get("welcome_message")
        if not ch_id or not msg:
            return
        ch = member.guild.get_channel(ch_id)
        if not ch:
            return
        text = (
            msg.replace("{user}", member.name)
            .replace("{user_mention}", member.mention)
            .replace("{server}", member.guild.name)
            .replace("{member_count}", str(member.guild.member_count))
        )
        try:
            await ch.send(text)
        except Exception:
            pass

    # ── Slash ────────────────────────────────────────────────────────────────
    @bot.tree.command(name="setcount", description="Set counting channel")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_setcount(interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_extra(db_path, interaction.guild.id, count_channel_id=channel.id, count_current=0, count_last_user=None)
        await interaction.followup.send(f"🔢 Counting → {channel.mention}")

    @bot.tree.command(name="sethoneypot", description="Set honeypot ban channel")
    @app_commands.default_permissions(administrator=True)
    async def slash_honeypot(interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_extra(db_path, interaction.guild.id, honeypot_channel_id=channel.id)
        await interaction.followup.send(f"🍯 Honeypot → {channel.mention}")

    @bot.tree.command(name="setwelcome", description="Set welcome channel + message")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_welcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_extra(db_path, interaction.guild.id, welcome_channel_id=channel.id, welcome_message=message)
        await interaction.followup.send(f"👋 Welcome set in {channel.mention}")

    @bot.tree.command(name="ticketpanel", description="Post a ticket panel")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_ticketpanel(interaction: discord.Interaction, title: str = "Support Tickets"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg = await get_extra(db_path, interaction.guild.id)
        if not cfg.get("ticket_category_id"):
            cat = await interaction.guild.create_category("Tickets")
            await set_extra(db_path, interaction.guild.id, ticket_category_id=cat.id)
        embed = discord.Embed(title=f"🎫 {title}", description="Click to open a ticket.", color=discord.Color.blurple())
        await interaction.followup.send(embed=embed, view=TicketView(db_path))

    @bot.tree.command(name="close", description="Close this ticket")
    async def slash_close(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not interaction.channel.topic or "ticket-user:" not in (interaction.channel.topic or ""):
            return await interaction.followup.send("Not a ticket.", ephemeral=True)
        await interaction.followup.send("Closing…")
        await asyncio.sleep(2)
        await interaction.channel.delete(reason=f"Closed by {interaction.user}")

    @bot.tree.command(name="gstart", description="Start a giveaway")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_gstart(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
        if not interaction.response.is_done():
            await interaction.response.defer()
        delta = parse_duration(duration)
        if not delta:
            return await interaction.followup.send("Duration like `10m` `1h`", ephemeral=True)
        ends = datetime.now(timezone.utc) + delta
        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(ends.timestamp())}:R>",
            color=discord.Color.gold(),
        )
        view = GiveawayView(db_path)
        msg = await interaction.followup.send(embed=embed, view=view)
        # followup may not return message with id in all cases
        if hasattr(msg, "id"):
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "INSERT INTO giveaways (message_id, channel_id, guild_id, prize, winners, ends_at, host_id) VALUES (?,?,?,?,?,?,?)",
                    (msg.id, interaction.channel.id, interaction.guild.id, prize, winners, ends.isoformat(), interaction.user.id),
                )
                await db.commit()
            asyncio.create_task(_end_giveaway_later(bot, db_path, msg.id, delta.total_seconds()))




    # ── Message counts ───────────────────────────────────────────────────────
    @bot.command(name="messages", aliases=["msgstats", "msgcount", "mymessages"])
    async def messages_cmd(ctx: commands.Context, member: discord.Member = None):
        """Show messages sent today / this week / this month / this year."""
        member = member or ctx.author
        if not ctx.guild:
            return await ctx.send("Server only.")
        await ensure_extra_tables(db_path)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        # week starts Monday
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        month_start = now.strftime("%Y-%m-01")
        year_start = now.strftime("%Y-01-01")
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT
                    COALESCE(SUM(CASE WHEN day = ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN day >= ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN day >= ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN day >= ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(count), 0)
                   FROM message_stats WHERE guild_id=? AND user_id=?""",
                (today, week_start, month_start, year_start, ctx.guild.id, member.id),
            ) as cur:
                row = await cur.fetchone()
        d, w, m, y, all_time = row or (0, 0, 0, 0, 0)
        embed = discord.Embed(
            title=f"💬 Messages · {member.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Today", value=f"**{d:,}**", inline=True)
        embed.add_field(name="This week", value=f"**{w:,}**", inline=True)
        embed.add_field(name="This month", value=f"**{m:,}**", inline=True)
        embed.add_field(name="This year", value=f"**{y:,}**", inline=True)
        embed.add_field(name="All tracked", value=f"**{all_time:,}**", inline=True)
        embed.set_footer(text="Counts start from when this feature was added · UTC days")
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @bot.tree.command(name="messages", description="Messages sent today / week / month / year")
    async def slash_messages(interaction: discord.Interaction, member: discord.Member = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        member = member or interaction.user
        if not interaction.guild:
            return await interaction.followup.send("Server only.", ephemeral=True)
        await ensure_extra_tables(db_path)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        month_start = now.strftime("%Y-%m-01")
        year_start = now.strftime("%Y-01-01")
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT
                    COALESCE(SUM(CASE WHEN day = ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN day >= ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN day >= ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN day >= ? THEN count ELSE 0 END), 0),
                    COALESCE(SUM(count), 0)
                   FROM message_stats WHERE guild_id=? AND user_id=?""",
                (today, week_start, month_start, year_start, interaction.guild.id, member.id),
            ) as cur:
                row = await cur.fetchone()
        d, w, m, y, all_time = row or (0, 0, 0, 0, 0)
        embed = discord.Embed(title=f"💬 Messages · {member.display_name}", color=discord.Color.blue())
        embed.add_field(name="Today", value=f"**{d:,}**", inline=True)
        embed.add_field(name="This week", value=f"**{w:,}**", inline=True)
        embed.add_field(name="This month", value=f"**{m:,}**", inline=True)
        embed.add_field(name="This year", value=f"**{y:,}**", inline=True)
        embed.add_field(name="All tracked", value=f"**{all_time:,}**", inline=True)
        embed.set_footer(text="Counts from when tracking started · UTC")
        await interaction.followup.send(embed=embed)


    # ── Message summary (recap of recent chat) ───────────────────────────────
    @bot.command(name="summary", aliases=["recap", "tldr", "summarize"])
    async def summary_cmd(ctx: commands.Context, amount: int = 50):
        """Summarize recent messages in this channel."""
        amount = max(10, min(amount, 100))
        status = await ctx.send(f"📝 Reading last **{amount}** messages…")
        messages = []
        async for msg in ctx.channel.history(limit=amount + 5):
            if msg.id == status.id:
                continue
            if msg.author.bot and not msg.content:
                continue
            content = (msg.content or "").strip()
            if not content and msg.embeds:
                content = msg.embeds[0].title or msg.embeds[0].description or "[embed]"
            if not content:
                continue
            messages.append(f"{msg.author.display_name}: {content[:300]}")
            if len(messages) >= amount:
                break
        messages.reverse()
        if len(messages) < 3:
            return await status.edit(content="Not enough messages to summarize.")
        text_block = "\n".join(messages)[:6000]
        summary = await _summarize_text(text_block)
        embed = discord.Embed(
            title=f"📝 Summary of last {len(messages)} messages",
            description=summary[:4000],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"#{ctx.channel.name}")
        await status.edit(content=None, embed=embed)

    @bot.tree.command(name="summary", description="Summarize recent messages in this channel")
    @app_commands.describe(amount="How many messages to read (10-100)")
    async def slash_summary(interaction: discord.Interaction, amount: app_commands.Range[int, 10, 100] = 50):
        if not interaction.response.is_done():
            await interaction.response.defer()
        messages = []
        async for msg in interaction.channel.history(limit=amount + 5):
            if msg.author.bot and not msg.content:
                continue
            content = (msg.content or "").strip()
            if not content and msg.embeds:
                content = (msg.embeds[0].title or msg.embeds[0].description or "[embed]")
            if not content:
                continue
            messages.append(f"{msg.author.display_name}: {content[:300]}")
            if len(messages) >= amount:
                break
        messages.reverse()
        if len(messages) < 3:
            return await interaction.followup.send("Not enough messages to summarize.")
        text_block = "\n".join(messages)[:6000]
        summary = await _summarize_text(text_block)
        embed = discord.Embed(
            title=f"📝 Summary of last {len(messages)} messages",
            description=summary[:4000],
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed)


    # ── AFK ──────────────────────────────────────────────────────────────────
    @bot.command(name="afk")
    async def afk_cmd(ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself AFK. Cleared when you talk again."""
        reason = reason[:200]
        await ensure_extra_tables(db_path)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """INSERT INTO afk_users (user_id, reason, since) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, since=excluded.since""",
                (ctx.author.id, reason, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        await ctx.send(f"💤 **{ctx.author.display_name}** is now AFK: {reason}")

    @bot.command(name="unafk", aliases=["back"])
    async def unafk_cmd(ctx: commands.Context):
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM afk_users WHERE user_id=?", (ctx.author.id,))
            await db.commit()
        await ctx.send(f"👋 Welcome back, {ctx.author.mention}!")

    # ── Timed messages ───────────────────────────────────────────────────────
    @bot.command(name="timedmsg", aliases=["schedule", "remind", "timer"])
    async def timedmsg_cmd(ctx: commands.Context, duration: str, *, message: str):
        """Schedule a message in this channel after a duration (e.g. 10m, 1h)."""
        delta = parse_duration(duration)
        if not delta:
            return await ctx.send("❌ Duration like `10m` `1h` `1d`")
        if delta > timedelta(days=30):
            return await ctx.send("❌ Max 30 days.")
        if len(message) > 1500:
            return await ctx.send("❌ Message too long (max 1500).")
        send_at = datetime.now(timezone.utc) + delta
        await ensure_extra_tables(db_path)
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                """INSERT INTO timed_messages (guild_id, channel_id, author_id, content, send_at)
                   VALUES (?,?,?,?,?)""",
                (ctx.guild.id if ctx.guild else 0, ctx.channel.id, ctx.author.id, message, send_at.isoformat()),
            )
            await db.commit()
            row_id = cur.lastrowid
        await ctx.send(
            f"⏰ Timed message **#{row_id}** scheduled for <t:{int(send_at.timestamp())}:R>\n> {message[:200]}"
        )
        asyncio.create_task(_send_timed_later(bot, db_path, row_id, delta.total_seconds()))

    @bot.command(name="timedlist", aliases=["timers"])
    async def timedlist_cmd(ctx: commands.Context):
        await ensure_extra_tables(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT id, content, send_at FROM timed_messages
                   WHERE guild_id=? AND sent=0 ORDER BY send_at LIMIT 15""",
                (ctx.guild.id if ctx.guild else 0,),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return await ctx.send("No pending timed messages.")
        lines = [f"**#{i}** <t:{int(datetime.fromisoformat(when).timestamp())}:R> — {msg[:80]}" for i, msg, when in rows]
        await ctx.send(embed=discord.Embed(title="⏰ Pending timed messages", description="\n".join(lines), color=discord.Color.blue()))

    @bot.command(name="timedcancel", aliases=["canceltimer"])
    async def timedcancel_cmd(ctx: commands.Context, msg_id: int):
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT author_id, sent FROM timed_messages WHERE id=?", (msg_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return await ctx.send("Not found.")
            if row[0] != ctx.author.id and not ctx.author.guild_permissions.manage_messages:
                return await ctx.send("❌ Only the author or a mod can cancel.")
            if row[1]:
                return await ctx.send("Already sent.")
            await db.execute("UPDATE timed_messages SET sent=1 WHERE id=?", (msg_id,))
            await db.commit()
        await ctx.send(f"🗑️ Cancelled timed message **#{msg_id}**")

    @bot.tree.command(name="afk", description="Set yourself AFK")
    async def slash_afk(interaction: discord.Interaction, reason: str = "AFK"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        reason = reason[:200]
        await ensure_extra_tables(db_path)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """INSERT INTO afk_users (user_id, reason, since) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, since=excluded.since""",
                (interaction.user.id, reason, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        await interaction.followup.send(f"💤 **{interaction.user.display_name}** is now AFK: {reason}")

    @bot.tree.command(name="unafk", description="Clear your AFK status")
    async def slash_unafk(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM afk_users WHERE user_id=?", (interaction.user.id,))
            await db.commit()
        await interaction.followup.send(f"👋 Welcome back, {interaction.user.mention}!")

    @bot.tree.command(name="timedmsg", description="Schedule a message after a duration")
    @app_commands.describe(duration="e.g. 10m, 1h, 1d", message="Message to send")
    async def slash_timedmsg(interaction: discord.Interaction, duration: str, message: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        delta = parse_duration(duration)
        if not delta or delta > timedelta(days=30):
            return await interaction.followup.send("❌ Duration like `10m` `1h` (max 30d)", ephemeral=True)
        send_at = datetime.now(timezone.utc) + delta
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                """INSERT INTO timed_messages (guild_id, channel_id, author_id, content, send_at)
                   VALUES (?,?,?,?,?)""",
                (interaction.guild.id, interaction.channel.id, interaction.user.id, message[:1500], send_at.isoformat()),
            )
            await db.commit()
            row_id = cur.lastrowid
        await interaction.followup.send(f"⏰ Timed message **#{row_id}** · <t:{int(send_at.timestamp())}:R>")
        asyncio.create_task(_send_timed_later(bot, db_path, row_id, delta.total_seconds()))

    @bot.tree.command(name="timedlist", description="List pending timed messages")
    async def slash_timedlist(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT id, content, send_at FROM timed_messages
                   WHERE guild_id=? AND sent=0 ORDER BY send_at LIMIT 15""",
                (interaction.guild.id,),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return await interaction.followup.send("No pending timed messages.")
        lines = [f"**#{i}** <t:{int(datetime.fromisoformat(when).timestamp())}:R> — {msg[:80]}" for i, msg, when in rows]
        await interaction.followup.send(embed=discord.Embed(title="⏰ Pending", description="\n".join(lines), color=discord.Color.blue()))

    @bot.tree.command(name="timedcancel", description="Cancel a timed message by ID")
    async def slash_timedcancel(interaction: discord.Interaction, msg_id: int):
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT author_id, sent FROM timed_messages WHERE id=?", (msg_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return await interaction.followup.send("Not found.", ephemeral=True)
            if row[0] != interaction.user.id and not interaction.user.guild_permissions.manage_messages:
                return await interaction.followup.send("❌ Only author or mod.", ephemeral=True)
            await db.execute("UPDATE timed_messages SET sent=1 WHERE id=?", (msg_id,))
            await db.commit()
        await interaction.followup.send(f"🗑️ Cancelled **#{msg_id}**")


    
    @bot.tree.command(name="count", description="Show next counting number")
    async def slash_count(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg = await get_extra(db_path, interaction.guild.id)
        ch = interaction.guild.get_channel(cfg.get("count_channel_id") or 0)
        await interaction.followup.send(f"Next number: **{(cfg.get('count_current') or 0) + 1}** in {ch.mention if ch else '`not set`'}")

    @bot.tree.command(name="unsethoneypot", description="Disable honeypot channel")
    @app_commands.default_permissions(administrator=True)
    async def slash_unsethoneypot(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_extra(db_path, interaction.guild.id, honeypot_channel_id=None)
        await interaction.followup.send("🍯 Honeypot disabled.")

    @bot.tree.command(name="testwelcome", description="Preview the welcome message")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_testwelcome(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg = await get_extra(db_path, interaction.guild.id)
        msg = cfg.get("welcome_message") or "Welcome {user_mention} to **{server}**!"
        text = (msg.replace("{user}", interaction.user.name)
                .replace("{user_mention}", interaction.user.mention)
                .replace("{server}", interaction.guild.name)
                .replace("{member_count}", str(interaction.guild.member_count)))
        await interaction.followup.send(text)

    @bot.tree.command(name="gend", description="End a giveaway by message ID")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_gend(interaction: discord.Interaction, message_id: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await _finish_giveaway(bot, db_path, int(message_id), interaction.channel)
        await interaction.followup.send("Giveaway end requested.")

    @bot.tree.command(name="greroll", description="Reroll giveaway winners")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_greroll(interaction: discord.Interaction, message_id: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        import random
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT winners FROM giveaways WHERE message_id=?", (int(message_id),)) as cur:
                row = await cur.fetchone()
            async with db.execute("SELECT user_id FROM giveaway_entries WHERE message_id=?", (int(message_id),)) as cur:
                entries = [r[0] for r in await cur.fetchall()]
        if not row or not entries:
            return await interaction.followup.send("No entries / giveaway.")
        w = min(row[0], len(entries))
        winners = random.sample(entries, w)
        await interaction.followup.send("🎉 Reroll: " + " ".join(f"<@{u}>" for u in winners))



    # ── Say ──────────────────────────────────────────────────────────────────
    @bot.command(name="say", aliases=["echo"])
    @commands.has_permissions(manage_messages=True)
    async def say_cmd(ctx: commands.Context, *, message: str):
        """Make the bot send a message."""
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.send(message[:2000])

    @bot.tree.command(name="say", description="Make the bot send a message")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(message="What the bot should say", channel="Optional channel to post in")
    async def slash_say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        ch = channel or interaction.channel
        await ch.send(message[:2000])
        await interaction.followup.send(f"✅ Sent in {ch.mention}", ephemeral=True)


    log.info("Extras (counting/gw/honeypot/tickets/welcome) registered")




async def _summarize_text(text_block: str) -> str:
    """Use Groq if available, else a simple extractive fallback."""
    prompt = (
        "Summarize this Discord chat in a short, clear bullet list. "
        "Note main topics, decisions, questions, and mood. Max 400 words.\n\n"
        + text_block
    )
    try:
        import os
        import httpx
        key = os.getenv("GROQ_API_KEY")
        model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        if key:
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "You summarize Discord chats concisely."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 500,
                    },
                )
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("AI summary failed: %s", e)
    # fallback: first/last lines
    lines = text_block.strip().split("\n")
    head = lines[:5]
    tail = lines[-5:] if len(lines) > 10 else []
    out = "**Recent activity (no AI key — raw sample):**\n"
    out += "\n".join(f"• {l[:120]}" for l in head)
    if tail:
        out += "\n…\n" + "\n".join(f"• {l[:120]}" for l in tail)
    return out


async def _send_timed_later(bot, db_path, row_id, seconds):
    await asyncio.sleep(max(1, seconds))
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT channel_id, author_id, content, sent FROM timed_messages WHERE id=?",
                (row_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row or row[3]:
                return
            await db.execute("UPDATE timed_messages SET sent=1 WHERE id=?", (row_id,))
            await db.commit()
        ch = bot.get_channel(row[0])
        if ch:
            await ch.send(f"⏰ <@{row[1]}>: {row[2]}")
    except Exception as e:
        log.warning("timed message %s failed: %s", row_id, e)


async def _end_giveaway_later(bot, db_path, message_id, seconds):
    await asyncio.sleep(max(1, seconds))
    await _finish_giveaway(bot, db_path, message_id, None)


async def _finish_giveaway(bot, db_path, message_id, channel):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT channel_id, prize, winners, ended FROM giveaways WHERE message_id=?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row or row[3]:
            return
        await db.execute("UPDATE giveaways SET ended=1 WHERE message_id=?", (message_id,))
        async with db.execute("SELECT user_id FROM giveaway_entries WHERE message_id=?", (message_id,)) as cur:
            entries = [r[0] for r in await cur.fetchall()]
        await db.commit()
    ch = channel or bot.get_channel(row[0])
    if not ch:
        return
    if not entries:
        await ch.send(f"Giveaway for **{row[1]}** ended — no entries.")
        return
    w = min(row[2], len(entries))
    winners = random.sample(entries, w)
    await ch.send(f"🎉 Congratulations {', '.join(f'<@{u}>' for u in winners)}! You won **{row[1]}**!")
