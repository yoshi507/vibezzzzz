"""
Circle-style Discord server event logging → a chosen #log channel.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands

try:
    from logging_setup import get_logger
    log = get_logger("vibezzzzz.serverlog")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.serverlog")


async def ensure_log_columns(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT DEFAULT 'v!',
                gambling_enabled INTEGER DEFAULT 1,
                trivia_enabled INTEGER DEFAULT 1
            )"""
        )
        # migrate columns if missing
        async with db.execute("PRAGMA table_info(guild_settings)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        for col, typedef in (
            ("log_channel_id", "INTEGER"),
            ("log_messages", "INTEGER DEFAULT 1"),
            ("log_members", "INTEGER DEFAULT 1"),
            ("log_moderation", "INTEGER DEFAULT 1"),
            ("log_server", "INTEGER DEFAULT 1"),
            ("log_voice", "INTEGER DEFAULT 1"),
        ):
            if col not in cols:
                await db.execute(f"ALTER TABLE guild_settings ADD COLUMN {col} {typedef}")
        await db.commit()


async def get_log_config(db_path: str, guild_id: int) -> dict:
    await ensure_log_columns(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """SELECT log_channel_id, log_messages, log_members, log_moderation, log_server, log_voice
               FROM guild_settings WHERE guild_id = ?""",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {
            "log_channel_id": None,
            "log_messages": True,
            "log_members": True,
            "log_moderation": True,
            "log_server": True,
            "log_voice": True,
        }
    return {
        "log_channel_id": row[0],
        "log_messages": bool(row[1] if row[1] is not None else 1),
        "log_members": bool(row[2] if row[2] is not None else 1),
        "log_moderation": bool(row[3] if row[3] is not None else 1),
        "log_server": bool(row[4] if row[4] is not None else 1),
        "log_voice": bool(row[5] if row[5] is not None else 1),
    }


async def set_log_channel(db_path: str, guild_id: int, channel_id: Optional[int]) -> None:
    await ensure_log_columns(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id""",
            (guild_id, channel_id),
        )
        await db.commit()
    log.info("Log channel for guild %s set to %s", guild_id, channel_id)


async def set_log_toggles(db_path: str, guild_id: int, **flags) -> None:
    await ensure_log_columns(db_path)
    allowed = ("log_messages", "log_members", "log_moderation", "log_server", "log_voice")
    sets = []
    vals = []
    for k, v in flags.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(1 if v else 0)
    if not sets:
        return
    vals.append(guild_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"""INSERT INTO guild_settings (guild_id) VALUES (?)
                ON CONFLICT(guild_id) DO UPDATE SET {', '.join(sets)}""",
            [guild_id] + vals[:-1] if False else None,
        )
        # simpler: ensure row then update
        await db.execute(
            "INSERT INTO guild_settings (guild_id) VALUES (?) ON CONFLICT DO NOTHING",
            (guild_id,),
        )
        await db.execute(
            f"UPDATE guild_settings SET {', '.join(sets)} WHERE guild_id = ?",
            vals,
        )
        await db.commit()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _user_line(user: discord.abc.User) -> str:
    return f"{user.mention} (`{user}` · `{user.id}`)"


async def send_log(
    bot: commands.Bot,
    db_path: str,
    guild: discord.Guild,
    category: str,
    embed: discord.Embed,
) -> None:
    """category: messages | members | moderation | server | voice"""
    if guild is None:
        return
    cfg = await get_log_config(db_path, guild.id)
    ch_id = cfg.get("log_channel_id")
    if not ch_id:
        return
    flag = {
        "messages": "log_messages",
        "members": "log_members",
        "moderation": "log_moderation",
        "server": "log_server",
        "voice": "log_voice",
    }.get(category)
    if flag and not cfg.get(flag, True):
        return
    channel = guild.get_channel(ch_id) or bot.get_channel(ch_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(ch_id)
        except Exception:
            return
    if not isinstance(channel, discord.TextChannel):
        return
    embed.set_footer(text=_ts())
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        log.warning("No permission to send logs in #%s (%s)", channel, ch_id)
    except Exception as e:
        log.warning("Failed to send server log: %s", e)


def register_server_log_events(bot: commands.Bot, db_path: str) -> None:
    """Attach all Circle-style listeners to the bot."""

    # ── Messages ─────────────────────────────────────────────────────────────
    @bot.event
    async def on_message_delete(message: discord.Message):
        if message.guild is None or (message.author and message.author.bot):
            return
        embed = discord.Embed(title="🗑️ Message Deleted", color=discord.Color.red())
        if message.author:
            embed.add_field(name="Author", value=_user_line(message.author), inline=False)
        embed.add_field(
            name="Channel",
            value=message.channel.mention if hasattr(message.channel, "mention") else str(message.channel),
            inline=True,
        )
        content = message.content or "*no text content*"
        if len(content) > 1000:
            content = content[:1000] + "…"
        embed.add_field(name="Content", value=content, inline=False)
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(a.url for a in message.attachments[:5]),
                inline=False,
            )
        embed.add_field(name="Message ID", value=str(message.id), inline=True)
        await send_log(bot, db_path, message.guild, "messages", embed)

    @bot.event
    async def on_message_edit(before: discord.Message, after: discord.Message):
        if before.guild is None or (before.author and before.author.bot):
            return
        if before.content == after.content:
            return
        embed = discord.Embed(
            title="✏️ Message Edited",
            color=discord.Color.orange(),
            url=after.jump_url,
        )
        if before.author:
            embed.add_field(name="Author", value=_user_line(before.author), inline=False)
        embed.add_field(
            name="Channel",
            value=before.channel.mention if hasattr(before.channel, "mention") else str(before.channel),
            inline=True,
        )
        old = before.content or "*empty*"
        new = after.content or "*empty*"
        if len(old) > 500:
            old = old[:500] + "…"
        if len(new) > 500:
            new = new[:500] + "…"
        embed.add_field(name="Before", value=old, inline=False)
        embed.add_field(name="After", value=new, inline=False)
        await send_log(bot, db_path, before.guild, "messages", embed)

    # ── Members ──────────────────────────────────────────────────────────────
    @bot.event
    async def on_member_join(member: discord.Member):
        embed = discord.Embed(title="📥 Member Joined", color=discord.Color.green())
        embed.add_field(name="User", value=_user_line(member), inline=False)
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.add_field(name="Member count", value=str(member.guild.member_count), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await send_log(bot, db_path, member.guild, "members", embed)

    @bot.event
    async def on_member_remove(member: discord.Member):
        embed = discord.Embed(title="📤 Member Left", color=discord.Color.dark_grey())
        embed.add_field(name="User", value=_user_line(member), inline=False)
        roles = [r.mention for r in member.roles if r.name != "@everyone"][:15]
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles), inline=False)
        embed.add_field(name="Member count", value=str(member.guild.member_count), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await send_log(bot, db_path, member.guild, "members", embed)

    @bot.event
    async def on_member_update(before: discord.Member, after: discord.Member):
        # Nickname
        if before.nick != after.nick:
            embed = discord.Embed(title="🏷️ Nickname Changed", color=discord.Color.blue())
            embed.add_field(name="User", value=_user_line(after), inline=False)
            embed.add_field(name="Before", value=before.nick or before.name, inline=True)
            embed.add_field(name="After", value=after.nick or after.name, inline=True)
            await send_log(bot, db_path, after.guild, "members", embed)
        # Roles
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        added = after_roles - before_roles
        removed = before_roles - after_roles
        if added or removed:
            embed = discord.Embed(title="🎭 Roles Updated", color=discord.Color.purple())
            embed.add_field(name="User", value=_user_line(after), inline=False)
            if added:
                embed.add_field(name="Added", value=" ".join(r.mention for r in added), inline=False)
            if removed:
                embed.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=False)
            await send_log(bot, db_path, after.guild, "members", embed)

    # ── Moderation ───────────────────────────────────────────────────────────
    @bot.event
    async def on_member_ban(guild: discord.Guild, user: discord.User):
        embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.dark_red())
        embed.add_field(name="User", value=_user_line(user), inline=False)
        reason = None
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == user.id:
                    reason = entry.reason
                    embed.add_field(name="Moderator", value=_user_line(entry.user), inline=True)
                    break
        except Exception:
            pass
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        await send_log(bot, db_path, guild, "moderation", embed)

    @bot.event
    async def on_member_unban(guild: discord.Guild, user: discord.User):
        embed = discord.Embed(title="🕊️ Member Unbanned", color=discord.Color.green())
        embed.add_field(name="User", value=_user_line(user), inline=False)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.unban):
                if entry.target and entry.target.id == user.id:
                    embed.add_field(name="Moderator", value=_user_line(entry.user), inline=True)
                    break
        except Exception:
            pass
        await send_log(bot, db_path, guild, "moderation", embed)

    # ── Server / channels / roles ────────────────────────────────────────────
    @bot.event
    async def on_guild_channel_create(channel):
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.ForumChannel)):
            return
        embed = discord.Embed(title="📁 Channel Created", color=discord.Color.green())
        embed.add_field(name="Name", value=getattr(channel, "mention", channel.name), inline=True)
        embed.add_field(name="Type", value=str(channel.type), inline=True)
        embed.add_field(name="ID", value=str(channel.id), inline=True)
        await send_log(bot, db_path, channel.guild, "server", embed)

    @bot.event
    async def on_guild_channel_delete(channel):
        if not hasattr(channel, "guild") or channel.guild is None:
            return
        embed = discord.Embed(title="📁 Channel Deleted", color=discord.Color.red())
        embed.add_field(name="Name", value=f"#{channel.name}", inline=True)
        embed.add_field(name="Type", value=str(getattr(channel, "type", "?")), inline=True)
        embed.add_field(name="ID", value=str(channel.id), inline=True)
        await send_log(bot, db_path, channel.guild, "server", embed)

    @bot.event
    async def on_guild_role_create(role: discord.Role):
        embed = discord.Embed(title="🎭 Role Created", color=role.color or discord.Color.green())
        embed.add_field(name="Name", value=role.name, inline=True)
        embed.add_field(name="ID", value=str(role.id), inline=True)
        await send_log(bot, db_path, role.guild, "server", embed)

    @bot.event
    async def on_guild_role_delete(role: discord.Role):
        embed = discord.Embed(title="🎭 Role Deleted", color=discord.Color.red())
        embed.add_field(name="Name", value=role.name, inline=True)
        embed.add_field(name="ID", value=str(role.id), inline=True)
        await send_log(bot, db_path, role.guild, "server", embed)

    @bot.event
    async def on_guild_update(before: discord.Guild, after: discord.Guild):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** {before.name} → {after.name}")
        if before.icon != after.icon:
            changes.append("**Icon** changed")
        if before.afk_channel != after.afk_channel:
            changes.append(f"**AFK channel** changed")
        if not changes:
            return
        embed = discord.Embed(title="🏠 Server Updated", color=discord.Color.blue(), description="\n".join(changes))
        await send_log(bot, db_path, after, "server", embed)

    # ── Voice ────────────────────────────────────────────────────────────────
    @bot.event
    async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title="🔊 Joined Voice", color=discord.Color.green())
            embed.add_field(name="User", value=_user_line(member), inline=False)
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            await send_log(bot, db_path, member.guild, "voice", embed)
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title="🔇 Left Voice", color=discord.Color.dark_grey())
            embed.add_field(name="User", value=_user_line(member), inline=False)
            embed.add_field(name="Channel", value=before.channel.name, inline=True)
            await send_log(bot, db_path, member.guild, "voice", embed)
        elif before.channel and after.channel:
            embed = discord.Embed(title="🔀 Moved Voice", color=discord.Color.blue())
            embed.add_field(name="User", value=_user_line(member), inline=False)
            embed.add_field(name="From", value=before.channel.name, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)
            await send_log(bot, db_path, member.guild, "voice", embed)

    log.info("Server event logging listeners registered")
