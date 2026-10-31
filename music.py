"""
Simple music player for Vibezzzzz (yt-dlp + FFmpeg).
Requires: pip install yt-dlp  and ffmpeg on the host.
"""
from __future__ import annotations

import os
from pathlib import Path

import asyncio
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

import discord
from discord.ext import commands
from discord import app_commands

try:
    from logging_setup import get_logger
    log = get_logger("vibezzzzz.music")
except Exception:
    import logging
    log = logging.getLogger("vibezzzzz.music")

# guild_id -> queue of dicts {title, url, webpage_url, requester}
_queues: Dict[int, Deque[dict]] = defaultdict(deque)
_playing: Dict[int, bool] = defaultdict(bool)

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def _find_cookies_file() -> str | None:
    """Look in several places — Wispbyte/Kerit cwd is often not the bot folder."""
    candidates = []
    env = os.getenv("YTDLP_COOKIES", "").strip()
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    cwd = Path.cwd()
    for folder in (here, cwd, Path("/home/container"), Path("/home/container/vibezzzzz-main")):
        candidates.append(folder / "cookies.txt")
        candidates.append(folder / "youtube_cookies.txt")
        candidates.append(folder / "cookies.netscape.txt")
    seen = set()
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        if rp.is_file() and rp.stat().st_size > 50:
            return str(rp)
    return None


def _ytdl_base_opts() -> dict:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "noplaylist": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "geo_bypass": True,
        "extractor_args": {"youtube": {"player_client": ["web", "ios", "android", "tv_embedded"]}},
    }
    cookie_path = _find_cookies_file()
    if cookie_path:
        opts["cookiefile"] = cookie_path
        # Log once
        if not getattr(_ytdl_base_opts, "_logged_cookies", False):
            _ytdl_base_opts._logged_cookies = True
            try:
                n = sum(1 for line in Path(cookie_path).read_text(errors="ignore").splitlines() if line and not line.startswith("#"))
                log.info("yt-dlp cookies loaded from %s (%d cookie lines)", cookie_path, n)
            except Exception:
                log.info("yt-dlp cookies loaded from %s", cookie_path)
    else:
        if not getattr(_ytdl_base_opts, "_logged_missing", False):
            _ytdl_base_opts._logged_missing = True
            log.warning(
                "No cookies.txt found — looked in bot folder, cwd, /home/container. "
                "YouTube will likely block datacenter IPs without cookies."
            )
    return opts


async def _spotify_to_query(url: str) -> str | None:
    """Resolve Spotify track/playlist URL to a search query via oEmbed."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://open.spotify.com/oembed", params={"url": url})
            if r.status_code == 200:
                title = (r.json() or {}).get("title") or ""
                if title:
                    return title
    except Exception as e:
        log.warning("spotify oembed failed: %s", e)
    return None


def _run_ytdl(query: str, clients=None):
    import yt_dlp
    opts = _ytdl_base_opts()
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if info is None:
            return None, "No results."
        if "entries" in info:
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                return None, "No search results."
            info = entries[0]
        stream = info.get("url")
        if not stream and info.get("formats"):
            for fmt in reversed(info["formats"]):
                if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
                    stream = fmt["url"]
                    break
            if not stream:
                for fmt in reversed(info["formats"]):
                    if fmt.get("url"):
                        stream = fmt["url"]
                        break
        if not stream:
            return None, "Found media but no playable audio stream."
        return {
            "title": info.get("title") or "Unknown",
            "url": stream,
            "webpage_url": info.get("webpage_url") or query,
            "duration": info.get("duration"),
            "source": info.get("extractor") or "unknown",
        }, None


async def _extract(query: str) -> tuple:
    """Return (info_dict | None, error_message | None). Tries YouTube then SoundCloud."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return None, "yt-dlp is not installed (`pip install yt-dlp`)."

    original = query.strip()
    loop = asyncio.get_event_loop()
    attempts = []
    last_err = None

    # Spotify → resolve title for search
    if "spotify.com" in original.lower():
        title = await _spotify_to_query(original)
        if title:
            attempts.append(("soundcloud", f"scsearch1:{title}"))
            attempts.append(("youtube", f"ytsearch1:{title}"))
        else:
            last_err = "Could not resolve Spotify link (try a song name instead)."
    elif "soundcloud.com" in original.lower():
        attempts.append(("soundcloud", original))
    elif original.startswith("http://") or original.startswith("https://"):
        attempts.append(("direct", original))
        attempts.append(("soundcloud", f"scsearch1:{original}"))
    else:
        # plain text search: SoundCloud first (works on blocked YT IPs), then YouTube
        attempts.append(("soundcloud", f"scsearch1:{original}"))
        attempts.append(("youtube", f"ytsearch1:{original}"))
        attempts.append(("youtube-android", f"ytsearch1:{original}"))

    client_map = {
        "youtube": ["web", "ios", "android"],
        "youtube-android": ["android", "ios"],
        "soundcloud": None,
        "direct": None,
    }

    for label, q in attempts:
        try:
            clients = client_map.get(label)
            result, err = await loop.run_in_executor(
                None, lambda q=q, c=clients: _run_ytdl(q, c)
            )
            if result:
                result["source"] = result.get("source") or label
                return result, None
            last_err = err
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue

    msg = str(last_err or "Unknown error")
    if "Sign in" in msg or "not a bot" in msg.lower():
        hint = (
            "YouTube blocked this IP. Tried SoundCloud fallback too. "
            "Use a SoundCloud URL, or song name (SoundCloud search), "
            "or fix cookies.txt for YouTube."
        )
        return None, f"{msg[:180]}\n\n💡 {hint}"
    if len(msg) > 350:
        msg = msg[:350] + "…"
    return None, msg


async def _play_next(bot: commands.Bot, guild: discord.Guild, channel: discord.abc.Messageable):
    q = _queues[guild.id]
    vc = guild.voice_client
    if not q or not vc:
        _playing[guild.id] = False
        return
    track = q.popleft()
    _playing[guild.id] = True

    def after_play(err):
        if err:
            log.warning("player error: %s", err)
        fut = asyncio.run_coroutine_threadsafe(_play_next(bot, guild, channel), bot.loop)
        try:
            fut.result(timeout=1)
        except Exception:
            pass

    try:
        source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTS)
        # volume transform optional
        vc.play(source, after=after_play)
        try:
            await channel.send(f"▶️ **{track['title']}** · requested by {track.get('requester', '?')}")
        except Exception:
            pass
    except FileNotFoundError:
        _playing[guild.id] = False
        try:
            await channel.send("❌ **ffmpeg** binary not found on PATH (system package, not pip).")
        except Exception:
            pass
    except Exception as e:
        log.warning("play failed: %s", e)
        try:
            await channel.send(f"❌ Could not play: `{type(e).__name__}: {e}`")
        except Exception:
            pass
        await _play_next(bot, guild, channel)


def register_music(bot: commands.Bot) -> None:
    # Startup cookie check (visible in console)
    cpath = _find_cookies_file()
    if cpath:
        msg = f"Music: cookies FOUND at {cpath}"
        log.info(msg)
        print(f"[vibezzzzz] {msg}")
    else:
        msg = (
            "Music: cookies NOT FOUND. Place cookies.txt next to bot.py "
            f"(cwd={Path.cwd()} bot_dir={Path(__file__).resolve().parent})"
        )
        log.warning(msg)
        print(f"[vibezzzzz] {msg}")


    @bot.command(name="join", aliases=["summon"])
    async def join_cmd(ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("Join a voice channel first.")
        ch = ctx.author.voice.channel
        if ctx.voice_client:
            await ctx.voice_client.move_to(ch)
        else:
            await ch.connect()
        await ctx.send(f"🔊 Joined **{ch.name}**")

    @bot.command(name="leave", aliases=["disconnect", "dc"])
    async def leave_cmd(ctx: commands.Context):
        if ctx.voice_client:
            _queues[ctx.guild.id].clear()
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Left voice.")
        else:
            await ctx.send("Not in voice.")

    @bot.command(name="play", aliases=["p"])
    async def play_cmd(ctx: commands.Context, *, query: str):
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("Join a voice channel first.")
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        status = await ctx.send("🔎 Searching…")
        cpath = _find_cookies_file()
        info, err = await _extract(query)
        if not info:
            extra = f"\n🍪 Cookies: `{cpath}`" if cpath else "\n🍪 Cookies: **not found** (put cookies.txt next to bot.py)"
            return await status.edit(content=f"❌ {err or 'Nothing found.'}{extra}")
        info["requester"] = ctx.author.display_name
        _queues[ctx.guild.id].append(info)
        src = info.get("source") or "?"
        await status.edit(content=f"➕ Queued **{info['title']}** · via `{src}`")
        if not ctx.voice_client.is_playing() and not _playing[ctx.guild.id]:
            await _play_next(bot, ctx.guild, ctx.channel)

    @bot.command(name="skip", aliases=["s", "next"])
    async def skip_cmd(ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("⏭️ Skipped.")
        else:
            await ctx.send("Nothing playing.")

    @bot.command(name="stop")
    async def stop_cmd(ctx: commands.Context):
        _queues[ctx.guild.id].clear()
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared queue.")

    @bot.command(name="queue", aliases=["q"])
    async def queue_cmd(ctx: commands.Context):
        q = list(_queues[ctx.guild.id])
        if not q:
            return await ctx.send("Queue is empty.")
        lines = [f"**{i+1}.** {t['title']}" for i, t in enumerate(q[:15])]
        await ctx.send(embed=discord.Embed(title="🎶 Queue", description="\n".join(lines), color=discord.Color.purple()))

    @bot.command(name="pause")
    async def pause_cmd(ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Paused.")

    @bot.command(name="resume")
    async def resume_cmd(ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Resumed.")

    # slash
    @bot.tree.command(name="play", description="Play music (YouTube search or URL)")
    async def slash_play(interaction: discord.Interaction, query: str):
        if not interaction.response.is_done():
            await interaction.response.defer()
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            return await interaction.followup.send("Join a voice channel first.", ephemeral=True)
        vc = interaction.guild.voice_client
        if not vc:
            await member.voice.channel.connect()
        cpath = _find_cookies_file()
        info, err = await _extract(query)
        if not info:
            extra = f"\n🍪 Cookies: `{cpath}`" if cpath else "\n🍪 Cookies: **not found** (put cookies.txt next to bot.py)"
            return await interaction.followup.send(f"❌ {err or 'Nothing found.'}{extra}")
        info["requester"] = interaction.user.display_name
        _queues[interaction.guild.id].append(info)
        src = info.get("source") or "?"
        await interaction.followup.send(f"➕ Queued **{info['title']}** · via `{src}`")
        vc = interaction.guild.voice_client
        if vc and not vc.is_playing() and not _playing[interaction.guild.id]:
            await _play_next(bot, interaction.guild, interaction.channel)

    @bot.tree.command(name="skip", description="Skip current track")
    async def slash_skip(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.followup.send("⏭️ Skipped.")
        else:
            await interaction.followup.send("Nothing playing.")

    @bot.tree.command(name="stop", description="Stop music and clear queue")
    async def slash_stop(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        _queues[interaction.guild.id].clear()
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await interaction.followup.send("⏹️ Stopped.")

    @bot.tree.command(name="queue", description="Show music queue")
    async def slash_queue(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        q = list(_queues[interaction.guild.id])
        if not q:
            return await interaction.followup.send("Queue empty.")
        lines = [f"**{i+1}.** {t['title']}" for i, t in enumerate(q[:15])]
        await interaction.followup.send(embed=discord.Embed(title="🎶 Queue", description="\n".join(lines), color=discord.Color.purple()))

    @bot.tree.command(name="join", description="Join your voice channel")
    async def slash_join(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice:
            return await interaction.followup.send("Join a voice channel first.", ephemeral=True)
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(member.voice.channel)
        else:
            await member.voice.channel.connect()
        await interaction.followup.send(f"🔊 Joined **{member.voice.channel.name}**")

    @bot.tree.command(name="leave", description="Leave voice channel")
    async def slash_leave(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if interaction.guild.voice_client:
            _queues[interaction.guild.id].clear()
            await interaction.guild.voice_client.disconnect()
            await interaction.followup.send("👋 Left voice.")
        else:
            await interaction.followup.send("Not in voice.")


    @bot.tree.command(name="pause", description="Pause current track")
    async def slash_pause(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.followup.send("⏸️ Paused.")
        else:
            await interaction.followup.send("Nothing playing.")

    @bot.tree.command(name="resume", description="Resume paused track")
    async def slash_resume(interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.followup.send("▶️ Resumed.")
        else:
            await interaction.followup.send("Nothing paused.")


    log.info("Music commands registered")
