"""
Anti-nuke, anti-raid, and verification for Vibezzzzz.
Note: Discord does not expose user IPs to bots — "IP ban" is not possible via API.
We use account age, join rate, and mass-action detection instead.
"""
from __future__ import annotations

import asyncio
import random
import string
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional, Set

import aiosqlite
import discord
from discord.ext import commands
from discord import app_commands, ui

try:
    from logging_setup import get_logger
    log = get_logger("vibezzzzz.security")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.security")


async def ensure_security_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS guild_security (
                guild_id INTEGER PRIMARY KEY,
                antinuke_on INTEGER DEFAULT 0,
                antiraid_on INTEGER DEFAULT 0,
                join_threshold INTEGER DEFAULT 8,
                join_window_sec INTEGER DEFAULT 12,
                min_account_days INTEGER DEFAULT 3,
                verify_on INTEGER DEFAULT 0,
                verify_role_id INTEGER,
                verify_channel_id INTEGER,
                quarantine_role_id INTEGER
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS antinuke_whitelist (
                guild_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (guild_id, user_id)
            )"""
        )
        await db.commit()


async def get_sec(db_path: str, guild_id: int) -> dict:
    await ensure_security_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT * FROM guild_security WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await db.execute("INSERT INTO guild_security (guild_id) VALUES (?)", (guild_id,))
                await db.commit()
                return {"guild_id": guild_id, "antinuke_on": 0, "antiraid_on": 0,
                        "join_threshold": 8, "join_window_sec": 12, "min_account_days": 3,
                        "verify_on": 0}
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


async def set_sec(db_path: str, guild_id: int, **kwargs) -> None:
    cfg = await get_sec(db_path, guild_id)
    cfg.update(kwargs)
    async with aiosqlite.connect(db_path) as db:
        # ensure autorole column exists
        try:
            async with db.execute("PRAGMA table_info(guild_security)") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            if "autorole_id" not in cols:
                await db.execute("ALTER TABLE guild_security ADD COLUMN autorole_id INTEGER")
        except Exception:
            pass
        await db.execute(
            """INSERT INTO guild_security (
                guild_id, antinuke_on, antiraid_on, join_threshold, join_window_sec,
                min_account_days, verify_on, verify_role_id, verify_channel_id, quarantine_role_id, autorole_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
                antinuke_on=excluded.antinuke_on,
                antiraid_on=excluded.antiraid_on,
                join_threshold=excluded.join_threshold,
                join_window_sec=excluded.join_window_sec,
                min_account_days=excluded.min_account_days,
                verify_on=excluded.verify_on,
                verify_role_id=excluded.verify_role_id,
                verify_channel_id=excluded.verify_channel_id,
                quarantine_role_id=excluded.quarantine_role_id,
                autorole_id=excluded.autorole_id
            """,
            (
                guild_id,
                int(cfg.get("antinuke_on") or 0),
                int(cfg.get("antiraid_on") or 0),
                int(cfg.get("join_threshold") or 8),
                int(cfg.get("join_window_sec") or 12),
                int(cfg.get("min_account_days") or 3),
                int(cfg.get("verify_on") or 0),
                cfg.get("verify_role_id"),
                cfg.get("verify_channel_id"),
                cfg.get("quarantine_role_id"),
                cfg.get("autorole_id"),
            ),
        )
        await db.commit()


# in-memory raid / nuke trackers
_joins: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=50))
_actions: Dict[int, Dict[int, Deque[float]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=20)))


class VerifyView(ui.View):
    def __init__(self, db_path: str):
        super().__init__(timeout=None)
        self.db_path = db_path

    @ui.button(label="✅ Verify", style=discord.ButtonStyle.green, custom_id="vibe_verify")
    async def verify_btn(self, interaction: discord.Interaction, button: ui.Button):
        cfg = await get_sec(self.db_path, interaction.guild.id)
        role_id = cfg.get("verify_role_id")
        if not role_id:
            return await interaction.response.send_message("Verification role not configured.", ephemeral=True)
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Verify role missing.", ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(interaction.user.id)
        try:
            await member.add_roles(role, reason="Verified")
            qid = cfg.get("quarantine_role_id")
            if qid:
                qr = interaction.guild.get_role(qid)
                if qr and qr in member.roles:
                    await member.remove_roles(qr, reason="Verified")
            await interaction.response.send_message("✅ You're verified!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I can't assign that role (move my role higher).", ephemeral=True)


def register_security(bot: commands.Bot, db_path: str) -> None:
    bot.add_view(VerifyView(db_path))

    # ── Commands ─────────────────────────────────────────────────────────────
    @bot.command(name="antinuke")
    @commands.has_permissions(administrator=True)
    async def antinuke_cmd(ctx: commands.Context, state: str = "status"):
        state = state.lower()
        if state in ("on", "enable", "true", "1"):
            await set_sec(db_path, ctx.guild.id, antinuke_on=1)
            await ctx.send("🛡️ **Anti-nuke enabled** — mass ban/kick/channel/role deletes will be stopped.")
        elif state in ("off", "disable", "false", "0"):
            await set_sec(db_path, ctx.guild.id, antinuke_on=0)
            await ctx.send("Anti-nuke disabled.")
        else:
            cfg = await get_sec(db_path, ctx.guild.id)
            await ctx.send(f"Anti-nuke: **{'ON' if cfg.get('antinuke_on') else 'OFF'}** · `v!antinuke on/off`")

    @bot.command(name="antiraid")
    @commands.has_permissions(administrator=True)
    async def antiraid_cmd(ctx: commands.Context, state: str = "status"):
        state = state.lower()
        if state in ("on", "enable", "true", "1"):
            await set_sec(db_path, ctx.guild.id, antiraid_on=1)
            await ctx.send("🚨 **Anti-raid enabled** — mass joins / new accounts will be handled.")
        elif state in ("off", "disable", "false", "0"):
            await set_sec(db_path, ctx.guild.id, antiraid_on=0)
            await ctx.send("Anti-raid disabled.")
        else:
            cfg = await get_sec(db_path, ctx.guild.id)
            await ctx.send(
                f"Anti-raid: **{'ON' if cfg.get('antiraid_on') else 'OFF'}** · "
                f"threshold {cfg.get('join_threshold')}/{cfg.get('join_window_sec')}s · "
                f"min account age {cfg.get('min_account_days')}d"
            )

    @bot.command(name="raidsettings")
    @commands.has_permissions(administrator=True)
    async def raidsettings(ctx: commands.Context, threshold: int = 8, window_sec: int = 12, min_days: int = 3):
        await set_sec(db_path, ctx.guild.id, join_threshold=threshold, join_window_sec=window_sec, min_account_days=min_days)
        await ctx.send(f"🚨 Raid settings: **{threshold}** joins / **{window_sec}s** · min account age **{min_days}** days")

    @bot.command(name="whitelist")
    @commands.has_permissions(administrator=True)
    async def whitelist_cmd(ctx: commands.Context, member: discord.Member):
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO antinuke_whitelist (guild_id, user_id) VALUES (?,?)",
                (ctx.guild.id, member.id),
            )
            await db.commit()
        await ctx.send(f"✅ Whitelisted {member.mention} from anti-nuke.")

    @bot.command(name="unwhitelist")
    @commands.has_permissions(administrator=True)
    async def unwhitelist_cmd(ctx: commands.Context, member: discord.Member):
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "DELETE FROM antinuke_whitelist WHERE guild_id=? AND user_id=?",
                (ctx.guild.id, member.id),
            )
            await db.commit()
        await ctx.send(f"Removed whitelist for {member.mention}.")

    @bot.command(name="setupverify", aliases=["verification"])
    @commands.has_permissions(administrator=True)
    async def setupverify(ctx: commands.Context, role: discord.Role = None):
        """Create verify role + panel. Members click to get the role."""
        guild = ctx.guild
        if role is None:
            role = discord.utils.get(guild.roles, name="Verified")
            if not role:
                role = await guild.create_role(name="Verified", reason="Verification setup")
        # quarantine (unverified) optional
        q = discord.utils.get(guild.roles, name="Unverified")
        if not q:
            q = await guild.create_role(name="Unverified", reason="Verification setup")
        await set_sec(db_path, guild.id, verify_on=1, verify_role_id=role.id, quarantine_role_id=q.id, verify_channel_id=ctx.channel.id)
        embed = discord.Embed(
            title="✅ Verification",
            description="Click the button below to verify and unlock the server.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, view=VerifyView(db_path))
        await ctx.send(f"Verified role: {role.mention} · Put this role below channels you want locked for unverified users.")

    @bot.command(name="verifyoff")
    @commands.has_permissions(administrator=True)
    async def verifyoff(ctx: commands.Context):
        await set_sec(db_path, ctx.guild.id, verify_on=0)
        await ctx.send("Verification disabled.")

    # slash
    @bot.tree.command(name="antinuke", description="Toggle anti-nuke")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def slash_antinuke(interaction: discord.Interaction, state: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        on = state.lower() in ("on", "enable", "true", "1")
        await set_sec(db_path, interaction.guild.id, antinuke_on=1 if on else 0)
        await interaction.followup.send(f"🛡️ Anti-nuke **{'ON' if on else 'OFF'}**")

    @bot.tree.command(name="antiraid", description="Toggle anti-raid")
    @app_commands.default_permissions(administrator=True)
    async def slash_antiraid(interaction: discord.Interaction, state: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        on = state.lower() in ("on", "enable", "true", "1")
        await set_sec(db_path, interaction.guild.id, antiraid_on=1 if on else 0)
        await interaction.followup.send(f"🚨 Anti-raid **{'ON' if on else 'OFF'}**")

    @bot.tree.command(name="setupverify", description="Post verification panel")
    @app_commands.default_permissions(administrator=True)
    async def slash_setupverify(interaction: discord.Interaction, role: discord.Role = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        guild = interaction.guild
        if role is None:
            role = discord.utils.get(guild.roles, name="Verified") or await guild.create_role(name="Verified")
        q = discord.utils.get(guild.roles, name="Unverified") or await guild.create_role(name="Unverified")
        await set_sec(db_path, guild.id, verify_on=1, verify_role_id=role.id, quarantine_role_id=q.id, verify_channel_id=interaction.channel.id)
        embed = discord.Embed(title="✅ Verification", description="Click to verify and unlock the server.", color=discord.Color.green())
        await interaction.followup.send(embed=embed, view=VerifyView(db_path))

    # ── Listeners ────────────────────────────────────────────────────────────
    @bot.listen("on_member_join")
    async def security_join(member: discord.Member):
        cfg = await get_sec(db_path, member.guild.id)
        # autorole
        if cfg.get("autorole_id"):
            role = member.guild.get_role(cfg["autorole_id"])
            if role:
                try:
                    await member.add_roles(role, reason="Autorole")
                except Exception as e:
                    log.warning("Autorole failed in %s: %s", member.guild.id, e)
        # verification quarantine
        if cfg.get("verify_on") and cfg.get("quarantine_role_id"):
            role = member.guild.get_role(cfg["quarantine_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="Awaiting verification")
                except Exception:
                    pass
        if not cfg.get("antiraid_on"):
            return
        now = asyncio.get_event_loop().time()
        q = _joins[member.guild.id]
        q.append(now)
        window = cfg.get("join_window_sec") or 12
        threshold = cfg.get("join_threshold") or 8
        # prune
        while q and now - q[0] > window:
            q.popleft()
        min_days = cfg.get("min_account_days") or 3
        age = (datetime.now(timezone.utc) - member.created_at).days
        # kick very new accounts during elevated joins
        if len(q) >= max(3, threshold // 2) and age < min_days:
            try:
                await member.kick(reason=f"Anti-raid: account {age}d old during join spike")
            except Exception:
                pass
            return
        if len(q) >= threshold:
            # lockdown @everyone send_messages
            try:
                for ch in list(member.guild.text_channels)[:30]:
                    ow = ch.overwrites_for(member.guild.default_role)
                    ow.send_messages = False
                    await ch.set_permissions(member.guild.default_role, overwrite=ow, reason="Anti-raid lockdown")
            except Exception:
                pass
            try:
                await member.kick(reason="Anti-raid: join threshold exceeded")
            except Exception:
                pass
            log.warning("Anti-raid triggered in %s (%s joins/%ss)", member.guild.id, len(q), window)

    async def _whitelisted(guild_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT 1 FROM antinuke_whitelist WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ) as cur:
                return await cur.fetchone() is not None

    async def _nuke_check(guild: discord.Guild, moderator_id: int, action: str):
        cfg = await get_sec(db_path, guild.id)
        if not cfg.get("antinuke_on"):
            return
        if moderator_id == guild.owner_id or moderator_id == bot.user.id:
            return
        if await _whitelisted(guild.id, moderator_id):
            return
        now = asyncio.get_event_loop().time()
        q = _actions[guild.id][moderator_id]
        q.append(now)
        while q and now - q[0] > 10:
            q.popleft()
        if len(q) < 3:
            return
        # punish: strip dangerous roles
        member = guild.get_member(moderator_id)
        if not member:
            return
        try:
            dangerous = [r for r in member.roles if r != guild.default_role and (
                r.permissions.administrator or r.permissions.ban_members or r.permissions.manage_channels
                or r.permissions.manage_roles or r.permissions.kick_members
            )]
            if dangerous:
                await member.remove_roles(*dangerous, reason=f"Anti-nuke: mass {action}")
            log.warning("Anti-nuke stripped %s in %s for mass %s", moderator_id, guild.id, action)
        except Exception as e:
            log.warning("Anti-nuke punish failed: %s", e)

    @bot.listen("on_member_remove")
    async def security_kick_track(member: discord.Member):
        # try audit for kick
        await asyncio.sleep(1)
        try:
            async for entry in member.guild.audit_logs(limit=3, action=discord.AuditLogAction.kick):
                if entry.target and entry.target.id == member.id and entry.user:
                    await _nuke_check(member.guild, entry.user.id, "kick")
                break
        except Exception:
            pass

    @bot.listen("on_member_ban")
    async def security_ban_track(guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=3, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == user.id and entry.user:
                    await _nuke_check(guild, entry.user.id, "ban")
                break
        except Exception:
            pass

    @bot.listen("on_guild_channel_delete")
    async def security_ch_del(channel):
        if not hasattr(channel, "guild"):
            return
        await asyncio.sleep(1)
        try:
            async for entry in channel.guild.audit_logs(limit=3, action=discord.AuditLogAction.channel_delete):
                if entry.user:
                    await _nuke_check(channel.guild, entry.user.id, "channel_delete")
                break
        except Exception:
            pass

    @bot.listen("on_guild_role_delete")
    async def security_role_del(role: discord.Role):
        await asyncio.sleep(1)
        try:
            async for entry in role.guild.audit_logs(limit=3, action=discord.AuditLogAction.role_delete):
                if entry.user:
                    await _nuke_check(role.guild, entry.user.id, "role_delete")
                break
        except Exception:
            pass


    @bot.tree.command(name="raidsettings", description="Configure anti-raid thresholds")
    @app_commands.default_permissions(administrator=True)
    async def slash_raidsettings(interaction: discord.Interaction, threshold: int = 8, window_sec: int = 12, min_days: int = 3):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_sec(db_path, interaction.guild.id, join_threshold=threshold, join_window_sec=window_sec, min_account_days=min_days)
        await interaction.followup.send(f"🚨 Raid settings: **{threshold}** joins / **{window_sec}s** · min age **{min_days}d**")

    @bot.tree.command(name="whitelist", description="Whitelist user from anti-nuke")
    @app_commands.default_permissions(administrator=True)
    async def slash_whitelist(interaction: discord.Interaction, member: discord.Member):
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT OR IGNORE INTO antinuke_whitelist (guild_id, user_id) VALUES (?,?)", (interaction.guild.id, member.id))
            await db.commit()
        await interaction.followup.send(f"✅ Whitelisted {member.mention}")

    @bot.tree.command(name="unwhitelist", description="Remove anti-nuke whitelist")
    @app_commands.default_permissions(administrator=True)
    async def slash_unwhitelist(interaction: discord.Interaction, member: discord.Member):
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM antinuke_whitelist WHERE guild_id=? AND user_id=?", (interaction.guild.id, member.id))
            await db.commit()
        await interaction.followup.send(f"Removed whitelist for {member.mention}")

    @bot.tree.command(name="verifyoff", description="Disable verification system")
    @app_commands.default_permissions(administrator=True)
    async def slash_verifyoff(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_sec(db_path, interaction.guild.id, verify_on=0)
        await interaction.followup.send("Verification disabled.")



    # ── Autorole ─────────────────────────────────────────────────────────────
    @bot.command(name="autorole", aliases=["setautorole", "autoroute"])
    @commands.has_permissions(manage_guild=True)
    async def autorole_cmd(ctx: commands.Context, role: discord.Role = None):
        """Set the role given automatically on join. Omit role to show current."""
        if role is None:
            cfg = await get_sec(db_path, ctx.guild.id)
            rid = cfg.get("autorole_id")
            r = ctx.guild.get_role(rid) if rid else None
            return await ctx.send(
                f"Autorole: {r.mention if r else '`not set`'}\n"
                f"Use `{ctx.prefix}autorole @Role` to set, `{ctx.prefix}autoroleoff` to clear."
            )
        if role >= ctx.guild.me.top_role:
            return await ctx.send("❌ That role is higher than or equal to my top role — move me above it.")
        if role.managed:
            return await ctx.send("❌ Can't use a managed/integration role.")
        await set_sec(db_path, ctx.guild.id, autorole_id=role.id)
        await ctx.send(f"✅ Autorole set to {role.mention} — new members will get it on join.")

    @bot.command(name="autoroleoff", aliases=["removeautorole", "clearautorole"])
    @commands.has_permissions(manage_guild=True)
    async def autoroleoff_cmd(ctx: commands.Context):
        await set_sec(db_path, ctx.guild.id, autorole_id=None)
        await ctx.send("✅ Autorole disabled.")

    @bot.tree.command(name="autorole", description="Set role given automatically when members join")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_autorole(interaction: discord.Interaction, role: discord.Role = None):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if role is None:
            cfg = await get_sec(db_path, interaction.guild.id)
            rid = cfg.get("autorole_id")
            r = interaction.guild.get_role(rid) if rid else None
            return await interaction.followup.send(f"Autorole: {r.mention if r else '`not set`'}")
        if role >= interaction.guild.me.top_role:
            return await interaction.followup.send("❌ Move my role above that role first.", ephemeral=True)
        await set_sec(db_path, interaction.guild.id, autorole_id=role.id)
        await interaction.followup.send(f"✅ Autorole set to {role.mention}")

    @bot.tree.command(name="autoroleoff", description="Disable autorole")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_autoroleoff(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        await set_sec(db_path, interaction.guild.id, autorole_id=None)
        await interaction.followup.send("✅ Autorole disabled.")


    log.info("Security (antinuke/antiraid/verify) registered")
