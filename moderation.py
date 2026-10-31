"""
Bleed-style moderation for Vibezzzzz:
  lock/unlock, jail, ban/softban/kick, timeout, warn, purge, slowmode, nick, hide
  + ,setupmod for jail role/channel + case logs
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands
from discord import app_commands

try:
    from logging_setup import get_logger
    log = get_logger("vibezzzzz.mod")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.mod")


# ── DB ───────────────────────────────────────────────────────────────────────

async def ensure_mod_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS guild_mod (
                guild_id INTEGER PRIMARY KEY,
                jail_role_id INTEGER,
                jail_channel_id INTEGER,
                case_log_id INTEGER,
                mute_role_id INTEGER,
                case_count INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                user_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                created_at TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS jail_records (
                guild_id INTEGER,
                user_id INTEGER,
                roles TEXT,
                jailed_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            )"""
        )
        await db.commit()


async def get_mod_config(db_path: str, guild_id: int) -> dict:
    await ensure_mod_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT jail_role_id, jail_channel_id, case_log_id, mute_role_id, case_count FROM guild_mod WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {}
    return {
        "jail_role_id": row[0],
        "jail_channel_id": row[1],
        "case_log_id": row[2],
        "mute_role_id": row[3],
        "case_count": row[4] or 0,
    }


async def save_mod_config(db_path: str, guild_id: int, **kwargs) -> None:
    await ensure_mod_tables(db_path)
    cfg = await get_mod_config(db_path, guild_id)
    cfg.update({k: v for k, v in kwargs.items() if v is not None or k in kwargs})
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO guild_mod (guild_id, jail_role_id, jail_channel_id, case_log_id, mute_role_id, case_count)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET
                 jail_role_id=excluded.jail_role_id,
                 jail_channel_id=excluded.jail_channel_id,
                 case_log_id=excluded.case_log_id,
                 mute_role_id=excluded.mute_role_id,
                 case_count=excluded.case_count""",
            (
                guild_id,
                cfg.get("jail_role_id"),
                cfg.get("jail_channel_id"),
                cfg.get("case_log_id"),
                cfg.get("mute_role_id"),
                cfg.get("case_count") or 0,
            ),
        )
        await db.commit()


async def next_case(db_path: str, guild_id: int) -> int:
    cfg = await get_mod_config(db_path, guild_id)
    n = (cfg.get("case_count") or 0) + 1
    await save_mod_config(db_path, guild_id, case_count=n)
    return n


async def add_warning(db_path: str, guild_id: int, user_id: int, mod_id: int, reason: str) -> int:
    await ensure_mod_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?,?,?,?,?)",
            (guild_id, user_id, mod_id, reason, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cur.lastrowid


async def get_warnings(db_path: str, guild_id: int, user_id: int) -> list:
    await ensure_mod_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id, moderator_id, reason, created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC",
            (guild_id, user_id),
        ) as cur:
            return await cur.fetchall()


async def clear_warnings(db_path: str, guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        await db.commit()
        return cur.rowcount


def parse_duration(text: str) -> Optional[timedelta]:
    """Parse 1h, 30m, 2d, 1w2d etc."""
    if not text:
        return None
    text = text.strip().lower()
    total = 0
    for num, unit in re.findall(r"(\d+)([smhdw])", text):
        n = int(num)
        total += n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    if total <= 0:
        return None
    return timedelta(seconds=total)


def hierarchy_ok(actor: discord.Member, target: discord.Member) -> bool:
    if actor.id == actor.guild.owner_id:
        return True
    if target.id == target.guild.owner_id:
        return False
    return actor.top_role > target.top_role


async def send_case(
    bot: commands.Bot,
    db_path: str,
    guild: discord.Guild,
    *,
    action: str,
    moderator: discord.abc.User,
    target: discord.abc.User,
    reason: str = "No reason provided",
    extra: str = "",
    color: discord.Color = discord.Color.orange(),
) -> int:
    case_id = await next_case(db_path, guild.id)
    cfg = await get_mod_config(db_path, guild.id)
    embed = discord.Embed(title=f"Case #{case_id} · {action}", color=color)
    embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if extra:
        embed.add_field(name="Details", value=extra, inline=False)
    embed.set_footer(text=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    ch_id = cfg.get("case_log_id")
    if ch_id:
        ch = guild.get_channel(ch_id) or bot.get_channel(ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
    # also try server_logs moderation channel
    try:
        from server_logs import send_log
        await send_log(bot, db_path, guild, "moderation", embed)
    except Exception:
        pass
    return case_id


# ── Registration ─────────────────────────────────────────────────────────────

def register_moderation(bot: commands.Bot, db_path: str) -> None:

    # ── setup ────────────────────────────────────────────────────────────────
    @bot.command(name="setupmod", aliases=["modsetup"])
    @commands.has_permissions(administrator=True)
    async def setupmod(ctx: commands.Context):
        """Create jail role, #jail channel, and #case-logs (Bleed-style)."""
        await ensure_mod_tables(db_path)
        guild = ctx.guild
        status = await ctx.send("⚙️ Setting up moderation…")

        # Jail role
        jail_role = discord.utils.get(guild.roles, name="Jailed")
        if not jail_role:
            jail_role = await guild.create_role(
                name="Jailed", colour=discord.Colour.dark_grey(), reason="Vibezzzzz mod setup"
            )
        # Deny jailed from all channels
        for ch in guild.channels:
            try:
                overwrite = ch.overwrites_for(jail_role)
                overwrite.view_channel = False
                overwrite.send_messages = False
                overwrite.connect = False
                await ch.set_permissions(jail_role, overwrite=overwrite, reason="Jail setup")
            except Exception:
                pass

        # Jail channel
        jail_ch = discord.utils.get(guild.text_channels, name="jail")
        if not jail_ch:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                jail_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                ),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            # staff can see
            for role in guild.roles:
                if role.permissions.moderate_members or role.permissions.manage_messages:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            jail_ch = await guild.create_text_channel("jail", overwrites=overwrites, reason="Vibezzzzz mod setup")

        # Case logs
        case_ch = discord.utils.get(guild.text_channels, name="case-logs")
        if not case_ch:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            for role in guild.roles:
                if role.permissions.moderate_members or role.permissions.view_audit_log:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
            case_ch = await guild.create_text_channel("case-logs", overwrites=overwrites, reason="Vibezzzzz mod setup")

        await save_mod_config(
            db_path, guild.id,
            jail_role_id=jail_role.id,
            jail_channel_id=jail_ch.id,
            case_log_id=case_ch.id,
        )
        await status.edit(
            content=None,
            embed=discord.Embed(
                title="✅ Moderation setup complete",
                description=(
                    f"**Jail role:** {jail_role.mention}\n"
                    f"**Jail channel:** {jail_ch.mention}\n"
                    f"**Case logs:** {case_ch.mention}\n\n"
                    f"Commands: `lock` `unlock` `jail` `unjail` `ban` `softban` `kick` "
                    f"`timeout` `warn` `purge` `slowmode` `nick` `hide`"
                ),
                color=discord.Color.green(),
            ),
        )

    # ── lock / unlock ────────────────────────────────────────────────────────
    @bot.command(name="lock", aliases=["lockdown"])
    @commands.has_permissions(manage_channels=True)
    async def lock_cmd(ctx: commands.Context, channel: discord.TextChannel = None, *, reason: str = "No reason provided"):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        overwrite.add_reactions = False
        overwrite.create_public_threads = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"{ctx.author}: {reason}")
        case = await send_case(bot, db_path, ctx.guild, action="Lock", moderator=ctx.author, target=ctx.author, reason=reason, extra=f"Channel: {channel.mention}", color=discord.Color.dark_grey())
        await ctx.send(f"🔒 Locked {channel.mention} · Case #{case}")

    @bot.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    async def unlock_cmd(ctx: commands.Context, channel: discord.TextChannel = None, *, reason: str = "No reason provided"):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None  # neutral
        overwrite.add_reactions = None
        overwrite.create_public_threads = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"{ctx.author}: {reason}")
        case = await send_case(bot, db_path, ctx.guild, action="Unlock", moderator=ctx.author, target=ctx.author, reason=reason, extra=f"Channel: {channel.mention}", color=discord.Color.green())
        await ctx.send(f"🔓 Unlocked {channel.mention} · Case #{case}")

    @bot.command(name="lockall")
    @commands.has_permissions(administrator=True)
    async def lockall_cmd(ctx: commands.Context, *, reason: str = "Server lockdown"):
        count = 0
        for ch in ctx.guild.text_channels:
            try:
                ow = ch.overwrites_for(ctx.guild.default_role)
                ow.send_messages = False
                await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=reason)
                count += 1
            except Exception:
                pass
        await ctx.send(f"🔒 Locked **{count}** channels.")

    @bot.command(name="unlockall")
    @commands.has_permissions(administrator=True)
    async def unlockall_cmd(ctx: commands.Context, *, reason: str = "Lockdown lifted"):
        count = 0
        for ch in ctx.guild.text_channels:
            try:
                ow = ch.overwrites_for(ctx.guild.default_role)
                ow.send_messages = None
                await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=reason)
                count += 1
            except Exception:
                pass
        await ctx.send(f"🔓 Unlocked **{count}** channels.")

    # ── hide / unhide ────────────────────────────────────────────────────────
    @bot.command(name="hide")
    @commands.has_permissions(manage_channels=True)
    async def hide_cmd(ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(f"👁️ Hidden {channel.mention} from @everyone")

    @bot.command(name="unhide")
    @commands.has_permissions(manage_channels=True)
    async def unhide_cmd(ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(f"👁️ Unhidden {channel.mention}")

    # ── jail ─────────────────────────────────────────────────────────────────
    @bot.command(name="jail")
    @commands.has_permissions(manage_messages=True)
    async def jail_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not hierarchy_ok(ctx.author, member):
            return await ctx.send("❌ You can't jail someone higher or equal in role hierarchy.")
        if member.id == ctx.author.id:
            return await ctx.send("❌ You can't jail yourself.")
        cfg = await get_mod_config(db_path, ctx.guild.id)
        role_id = cfg.get("jail_role_id")
        if not role_id:
            return await ctx.send("❌ Jail not set up. Run `v!setupmod` first.")
        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.send("❌ Jail role missing. Run `v!setupmod` again.")

        # save roles & strip
        role_ids = [r.id for r in member.roles if r != ctx.guild.default_role and not r.managed]
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """INSERT INTO jail_records (guild_id, user_id, roles, jailed_at) VALUES (?,?,?,?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET roles=excluded.roles, jailed_at=excluded.jailed_at""",
                (ctx.guild.id, member.id, ",".join(map(str, role_ids)), datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        try:
            await member.edit(roles=[role], reason=f"Jailed by {ctx.author}: {reason}")
        except discord.Forbidden:
            return await ctx.send("❌ I can't edit that member's roles (move my role higher).")
        case = await send_case(bot, db_path, ctx.guild, action="Jail", moderator=ctx.author, target=member, reason=reason, color=discord.Color.dark_grey())
        await ctx.send(f"🔒 Jailed {member.mention} · Case #{case}")
        jail_ch = ctx.guild.get_channel(cfg.get("jail_channel_id") or 0)
        if jail_ch:
            try:
                await jail_ch.send(f"{member.mention} has been jailed. Reason: {reason}")
            except Exception:
                pass

    @bot.command(name="unjail")
    @commands.has_permissions(manage_messages=True)
    async def unjail_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        cfg = await get_mod_config(db_path, ctx.guild.id)
        role_id = cfg.get("jail_role_id")
        role = ctx.guild.get_role(role_id) if role_id else None
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT roles FROM jail_records WHERE guild_id=? AND user_id=?",
                (ctx.guild.id, member.id),
            ) as cur:
                row = await cur.fetchone()
            await db.execute(
                "DELETE FROM jail_records WHERE guild_id=? AND user_id=?",
                (ctx.guild.id, member.id),
            )
            await db.commit()
        restore = []
        if row and row[0]:
            for rid in row[0].split(","):
                if rid.isdigit():
                    r = ctx.guild.get_role(int(rid))
                    if r:
                        restore.append(r)
        try:
            if role and role in member.roles:
                await member.remove_roles(role, reason=f"Unjail: {reason}")
            if restore:
                await member.add_roles(*restore, reason="Restore roles after unjail")
        except discord.Forbidden:
            return await ctx.send("❌ Missing permissions to restore roles.")
        case = await send_case(bot, db_path, ctx.guild, action="Unjail", moderator=ctx.author, target=member, reason=reason, color=discord.Color.green())
        await ctx.send(f"🔓 Unjailed {member.mention} · Case #{case}")

    @bot.command(name="jaillist")
    @commands.has_permissions(manage_messages=True)
    async def jaillist_cmd(ctx: commands.Context):
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT user_id, jailed_at FROM jail_records WHERE guild_id=?",
                (ctx.guild.id,),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return await ctx.send("No one is jailed.")
        lines = []
        for uid, when in rows:
            m = ctx.guild.get_member(uid)
            lines.append(f"• {m.mention if m else uid} — since {when[:16]}")
        await ctx.send(embed=discord.Embed(title="🔒 Jailed members", description="\n".join(lines), color=discord.Color.dark_grey()))

    # ── ban / softban / unban / kick ─────────────────────────────────────────
    @bot.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not hierarchy_ok(ctx.author, member):
            return await ctx.send("❌ Hierarchy check failed.")
        await member.ban(reason=f"{ctx.author}: {reason}", delete_message_days=0)
        case = await send_case(bot, db_path, ctx.guild, action="Ban", moderator=ctx.author, target=member, reason=reason, color=discord.Color.red())
        await ctx.send(f"🔨 Banned {member} · Case #{case}")

    @bot.command(name="softban")
    @commands.has_permissions(ban_members=True)
    async def softban_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not hierarchy_ok(ctx.author, member):
            return await ctx.send("❌ Hierarchy check failed.")
        await member.ban(reason=f"Softban by {ctx.author}: {reason}", delete_message_days=1)
        await ctx.guild.unban(member, reason="Softban unban")
        case = await send_case(bot, db_path, ctx.guild, action="Softban", moderator=ctx.author, target=member, reason=reason, color=discord.Color.orange())
        await ctx.send(f"🧹 Softbanned {member} (messages purged) · Case #{case}")

    @bot.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban_cmd(ctx: commands.Context, user_id: int, *, reason: str = "No reason provided"):
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
        case = await send_case(bot, db_path, ctx.guild, action="Unban", moderator=ctx.author, target=user, reason=reason, color=discord.Color.green())
        await ctx.send(f"🕊️ Unbanned {user} · Case #{case}")

    @bot.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not hierarchy_ok(ctx.author, member):
            return await ctx.send("❌ Hierarchy check failed.")
        await member.kick(reason=f"{ctx.author}: {reason}")
        case = await send_case(bot, db_path, ctx.guild, action="Kick", moderator=ctx.author, target=member, reason=reason, color=discord.Color.orange())
        await ctx.send(f"👢 Kicked {member} · Case #{case}")

    # ── timeout ──────────────────────────────────────────────────────────────
    @bot.command(name="timeout", aliases=["mute", "to"])
    @commands.has_permissions(moderate_members=True)
    async def timeout_cmd(ctx: commands.Context, member: discord.Member, duration: str = "1h", *, reason: str = "No reason provided"):
        if not hierarchy_ok(ctx.author, member):
            return await ctx.send("❌ Hierarchy check failed.")
        delta = parse_duration(duration)
        if not delta:
            return await ctx.send("❌ Duration like `30m` `1h` `1d` `1w`")
        if delta > timedelta(days=28):
            return await ctx.send("❌ Discord max timeout is 28 days.")
        until = datetime.now(timezone.utc) + delta
        await member.timeout(until, reason=f"{ctx.author}: {reason}")
        case = await send_case(bot, db_path, ctx.guild, action="Timeout", moderator=ctx.author, target=member, reason=reason, extra=f"Duration: {duration}", color=discord.Color.blue())
        await ctx.send(f"⏱️ Timed out {member.mention} for **{duration}** · Case #{case}")

    @bot.command(name="untimeout", aliases=["unmute"])
    @commands.has_permissions(moderate_members=True)
    async def untimeout_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await member.timeout(None, reason=f"{ctx.author}: {reason}")
        case = await send_case(bot, db_path, ctx.guild, action="Untimeout", moderator=ctx.author, target=member, reason=reason, color=discord.Color.green())
        await ctx.send(f"✅ Removed timeout from {member.mention} · Case #{case}")

    # ── warn ─────────────────────────────────────────────────────────────────
    @bot.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def warn_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        wid = await add_warning(db_path, ctx.guild.id, member.id, ctx.author.id, reason)
        warns = await get_warnings(db_path, ctx.guild.id, member.id)
        case = await send_case(bot, db_path, ctx.guild, action="Warn", moderator=ctx.author, target=member, reason=reason, extra=f"Warning #{wid} · Total: {len(warns)}", color=discord.Color.gold())
        await ctx.send(f"⚠️ Warned {member.mention} · **{len(warns)}** total · Case #{case}")
        try:
            await member.send(f"You were warned in **{ctx.guild.name}**: {reason}")
        except Exception:
            pass

    @bot.command(name="warnings", aliases=["warns"])
    async def warnings_cmd(ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        warns = await get_warnings(db_path, ctx.guild.id, member.id)
        if not warns:
            return await ctx.send(f"{member.display_name} has no warnings.")
        lines = [f"**#{i}** <@{mod}> — {reason} _( {when[:16]} )_" for i, mod, reason, when in warns[:15]]
        await ctx.send(embed=discord.Embed(title=f"Warnings · {member}", description="\n".join(lines), color=discord.Color.gold()))

    @bot.command(name="clearwarns", aliases=["delwarns"])
    @commands.has_permissions(manage_messages=True)
    async def clearwarns_cmd(ctx: commands.Context, member: discord.Member):
        n = await clear_warnings(db_path, ctx.guild.id, member.id)
        await ctx.send(f"🗑️ Cleared **{n}** warnings for {member.mention}")

    # ── purge ────────────────────────────────────────────────────────────────
    @bot.command(name="purge", aliases=["clear", "prune"])
    @commands.has_permissions(manage_messages=True)
    async def purge_cmd(ctx: commands.Context, amount: int = 10, member: discord.Member = None):
        if amount < 1 or amount > 100:
            return await ctx.send("Amount 1–100.")
        def check(m):
            return member is None or m.author.id == member.id
        deleted = await ctx.channel.purge(limit=amount + 1, check=check)
        msg = await ctx.send(f"🧹 Deleted **{len(deleted)-1}** messages.", delete_after=5)
        await send_case(bot, db_path, ctx.guild, action="Purge", moderator=ctx.author, target=member or ctx.author, reason=f"{len(deleted)-1} msgs in #{ctx.channel.name}", color=discord.Color.purple())

    # ── slowmode / nick ──────────────────────────────────────────────────────
    @bot.command(name="slowmode", aliases=["slow"])
    @commands.has_permissions(manage_channels=True)
    async def slowmode_cmd(ctx: commands.Context, seconds: int = 0, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        if seconds < 0 or seconds > 21600:
            return await ctx.send("0–21600 seconds.")
        await channel.edit(slowmode_delay=seconds)
        await ctx.send(f"🐌 Slowmode in {channel.mention}: **{seconds}s**")

    @bot.command(name="nick", aliases=["setnick", "nickname"])
    @commands.has_permissions(manage_nicknames=True)
    async def nick_cmd(ctx: commands.Context, member: discord.Member, *, nick: str = None):
        if not hierarchy_ok(ctx.author, member) and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send("❌ Hierarchy check failed.")
        await member.edit(nick=nick)
        await ctx.send(f"🏷️ Nickname for {member.mention} → **{nick or '(reset)'}**")

    # ── Slash mirrors (main ones) ────────────────────────────────────────────
    @bot.tree.command(name="lock", description="Lock a channel")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_lock(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        channel = channel or interaction.channel
        ow = channel.overwrites_for(interaction.guild.default_role)
        ow.send_messages = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=ow, reason=reason)
        case = await send_case(bot, db_path, interaction.guild, action="Lock", moderator=interaction.user, target=interaction.user, reason=reason, extra=channel.mention, color=discord.Color.dark_grey())
        await interaction.followup.send(f"🔒 Locked {channel.mention} · Case #{case}")

    @bot.tree.command(name="unlock", description="Unlock a channel")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_unlock(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        channel = channel or interaction.channel
        ow = channel.overwrites_for(interaction.guild.default_role)
        ow.send_messages = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=ow, reason=reason)
        await interaction.followup.send(f"🔓 Unlocked {channel.mention}")

    @bot.tree.command(name="jail", description="Jail a member")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_jail(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        # reuse prefix logic via invoking is hard; duplicate minimal
        if not hierarchy_ok(interaction.user, member):
            return await interaction.followup.send("❌ Hierarchy check failed.", ephemeral=True)
        cfg = await get_mod_config(db_path, interaction.guild.id)
        role = interaction.guild.get_role(cfg.get("jail_role_id") or 0)
        if not role:
            return await interaction.followup.send("❌ Run `v!setupmod` first.", ephemeral=True)
        role_ids = [r.id for r in member.roles if r != interaction.guild.default_role and not r.managed]
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """INSERT INTO jail_records (guild_id, user_id, roles, jailed_at) VALUES (?,?,?,?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET roles=excluded.roles""",
                (interaction.guild.id, member.id, ",".join(map(str, role_ids)), datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        await member.edit(roles=[role], reason=reason)
        case = await send_case(bot, db_path, interaction.guild, action="Jail", moderator=interaction.user, target=member, reason=reason, color=discord.Color.dark_grey())
        await interaction.followup.send(f"🔒 Jailed {member.mention} · Case #{case}")

    @bot.tree.command(name="ban", description="Ban a member")
    @app_commands.default_permissions(ban_members=True)
    async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not hierarchy_ok(interaction.user, member):
            return await interaction.followup.send("❌ Hierarchy check failed.", ephemeral=True)
        await member.ban(reason=reason)
        case = await send_case(bot, db_path, interaction.guild, action="Ban", moderator=interaction.user, target=member, reason=reason, color=discord.Color.red())
        await interaction.followup.send(f"🔨 Banned {member} · Case #{case}")

    @bot.tree.command(name="kick", description="Kick a member")
    @app_commands.default_permissions(kick_members=True)
    async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not hierarchy_ok(interaction.user, member):
            return await interaction.followup.send("❌ Hierarchy check failed.", ephemeral=True)
        await member.kick(reason=reason)
        case = await send_case(bot, db_path, interaction.guild, action="Kick", moderator=interaction.user, target=member, reason=reason)
        await interaction.followup.send(f"👢 Kicked {member} · Case #{case}")

    @bot.tree.command(name="timeout", description="Timeout a member")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_timeout(interaction: discord.Interaction, member: discord.Member, duration: str = "1h", reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        delta = parse_duration(duration)
        if not delta or delta > timedelta(days=28):
            return await interaction.followup.send("❌ Duration like `30m` `1h` `1d` (max 28d)", ephemeral=True)
        await member.timeout(datetime.now(timezone.utc) + delta, reason=reason)
        case = await send_case(bot, db_path, interaction.guild, action="Timeout", moderator=interaction.user, target=member, reason=reason, extra=duration)
        await interaction.followup.send(f"⏱️ Timed out {member.mention} for **{duration}** · Case #{case}")

    @bot.tree.command(name="warn", description="Warn a member")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await add_warning(db_path, interaction.guild.id, member.id, interaction.user.id, reason)
        warns = await get_warnings(db_path, interaction.guild.id, member.id)
        case = await send_case(bot, db_path, interaction.guild, action="Warn", moderator=interaction.user, target=member, reason=reason, extra=f"Total: {len(warns)}")
        await interaction.followup.send(f"⚠️ Warned {member.mention} · **{len(warns)}** total · Case #{case}")

    @bot.tree.command(name="purge", description="Delete recent messages")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100] = 10):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** messages.", ephemeral=True)

    @bot.tree.command(name="setupmod", description="Create jail role, #jail, #case-logs")
    @app_commands.default_permissions(administrator=True)
    async def slash_setupmod(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        # trigger via prefix-style: create minimal
        await interaction.followup.send("Run **`v!setupmod`** in a channel for full setup (creates roles + channels).")


    @bot.tree.command(name="softban", description="Softban (ban + unban to purge messages)")
    @app_commands.default_permissions(ban_members=True)
    async def slash_softban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not hierarchy_ok(interaction.user, member):
            return await interaction.followup.send("❌ Hierarchy check failed.", ephemeral=True)
        await member.ban(reason=f"Softban: {reason}", delete_message_days=1)
        await interaction.guild.unban(member, reason="Softban unban")
        case = await send_case(bot, db_path, interaction.guild, action="Softban", moderator=interaction.user, target=member, reason=reason, color=discord.Color.orange())
        await interaction.followup.send(f"🧹 Softbanned {member} · Case #{case}")

    @bot.tree.command(name="unban", description="Unban a user by ID")
    @app_commands.default_permissions(ban_members=True)
    async def slash_unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        case = await send_case(bot, db_path, interaction.guild, action="Unban", moderator=interaction.user, target=user, reason=reason, color=discord.Color.green())
        await interaction.followup.send(f"🕊️ Unbanned {user} · Case #{case}")

    @bot.tree.command(name="unjail", description="Remove a member from jail")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_unjail(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg = await get_mod_config(db_path, interaction.guild.id)
        role = interaction.guild.get_role(cfg.get("jail_role_id") or 0)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT roles FROM jail_records WHERE guild_id=? AND user_id=?", (interaction.guild.id, member.id)) as cur:
                row = await cur.fetchone()
            await db.execute("DELETE FROM jail_records WHERE guild_id=? AND user_id=?", (interaction.guild.id, member.id))
            await db.commit()
        if role and role in member.roles:
            await member.remove_roles(role, reason=reason)
        if row and row[0]:
            restore = [interaction.guild.get_role(int(r)) for r in row[0].split(",") if r.isdigit()]
            restore = [r for r in restore if r]
            if restore:
                await member.add_roles(*restore, reason="Unjail restore")
        case = await send_case(bot, db_path, interaction.guild, action="Unjail", moderator=interaction.user, target=member, reason=reason, color=discord.Color.green())
        await interaction.followup.send(f"🔓 Unjailed {member.mention} · Case #{case}")

    @bot.tree.command(name="jaillist", description="List jailed members")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_jaillist(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT user_id, jailed_at FROM jail_records WHERE guild_id=?", (interaction.guild.id,)) as cur:
                rows = await cur.fetchall()
        if not rows:
            return await interaction.followup.send("No one is jailed.")
        lines = [f"• <@{uid}> — {when[:16]}" for uid, when in rows]
        await interaction.followup.send(embed=discord.Embed(title="🔒 Jailed", description="\n".join(lines), color=discord.Color.dark_grey()))

    @bot.tree.command(name="untimeout", description="Remove timeout from a member")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_untimeout(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await member.timeout(None, reason=reason)
        await interaction.followup.send(f"✅ Removed timeout from {member.mention}")

    @bot.tree.command(name="warnings", description="View warnings for a member")
    async def slash_warnings(interaction: discord.Interaction, member: discord.Member = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        member = member or interaction.user
        warns = await get_warnings(db_path, interaction.guild.id, member.id)
        if not warns:
            return await interaction.followup.send(f"{member.display_name} has no warnings.")
        lines = [f"**#{i}** — {reason}" for i, mod, reason, when in warns[:15]]
        await interaction.followup.send(embed=discord.Embed(title=f"Warnings · {member}", description="\n".join(lines), color=discord.Color.gold()))

    @bot.tree.command(name="clearwarns", description="Clear all warnings for a member")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_clearwarns(interaction: discord.Interaction, member: discord.Member):
        if not interaction.response.is_done():
            await interaction.response.defer()
        n = await clear_warnings(db_path, interaction.guild.id, member.id)
        await interaction.followup.send(f"🗑️ Cleared **{n}** warnings for {member.mention}")

    @bot.tree.command(name="lockall", description="Lock all text channels")
    @app_commands.default_permissions(administrator=True)
    async def slash_lockall(interaction: discord.Interaction, reason: str = "Server lockdown"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        count = 0
        for ch in interaction.guild.text_channels:
            try:
                ow = ch.overwrites_for(interaction.guild.default_role)
                ow.send_messages = False
                await ch.set_permissions(interaction.guild.default_role, overwrite=ow, reason=reason)
                count += 1
            except Exception:
                pass
        await interaction.followup.send(f"🔒 Locked **{count}** channels.")

    @bot.tree.command(name="unlockall", description="Unlock all text channels")
    @app_commands.default_permissions(administrator=True)
    async def slash_unlockall(interaction: discord.Interaction, reason: str = "Lockdown lifted"):
        if not interaction.response.is_done():
            await interaction.response.defer()
        count = 0
        for ch in interaction.guild.text_channels:
            try:
                ow = ch.overwrites_for(interaction.guild.default_role)
                ow.send_messages = None
                await ch.set_permissions(interaction.guild.default_role, overwrite=ow, reason=reason)
                count += 1
            except Exception:
                pass
        await interaction.followup.send(f"🔓 Unlocked **{count}** channels.")

    @bot.tree.command(name="hide", description="Hide channel from @everyone")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_hide(interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        channel = channel or interaction.channel
        ow = channel.overwrites_for(interaction.guild.default_role)
        ow.view_channel = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=ow)
        await interaction.followup.send(f"👁️ Hidden {channel.mention}")

    @bot.tree.command(name="unhide", description="Unhide channel for @everyone")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_unhide(interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        channel = channel or interaction.channel
        ow = channel.overwrites_for(interaction.guild.default_role)
        ow.view_channel = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=ow)
        await interaction.followup.send(f"👁️ Unhidden {channel.mention}")

    @bot.tree.command(name="slowmode", description="Set channel slowmode (seconds)")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600] = 0, channel: discord.TextChannel = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        channel = channel or interaction.channel
        await channel.edit(slowmode_delay=seconds)
        await interaction.followup.send(f"🐌 Slowmode in {channel.mention}: **{seconds}s**")

    @bot.tree.command(name="nick", description="Change a member nickname")
    @app_commands.default_permissions(manage_nicknames=True)
    async def slash_nick(interaction: discord.Interaction, member: discord.Member, nick: str = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await member.edit(nick=nick)
        await interaction.followup.send(f"🏷️ {member.mention} → **{nick or '(reset)'}**")


    log.info("Bleed-style moderation commands registered")
