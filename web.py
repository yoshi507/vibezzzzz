"""
Vibezzzzz Dashboard - FastAPI + Discord OAuth
Runs in the same process as the bot.
"""
import os
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, quote

import aiosqlite
import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "vibezzzzz.db")
TEMPLATES_DIR = str(BASE_DIR / "templates")

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("DASHBOARD_REDIRECT_URI", "http://localhost:8080/callback")
SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", secrets.token_hex(32))
API_BASE = "https://discord.com/api/v10"

OWNER_IDS = {
    x.strip()
    for x in os.getenv("BOT_OWNER_IDS", "").split(",")
    if x.strip()
}

app = FastAPI(title="Vibezzzzz Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 7)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


def is_owner(user: Optional[dict]) -> bool:
    if not user:
        return False
    return str(user.get("id", "")) in OWNER_IDS


async def ensure_db():
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


async def get_stats():
    await ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(balance),0), COALESCE(SUM(wins),0), COALESCE(SUM(losses),0) FROM users"
        ) as cur:
            row = await cur.fetchone()
            return {
                "total_users": row[0] or 0,
                "total_vibes": row[1] or 0,
                "total_wins": row[2] or 0,
                "total_losses": row[3] or 0,
            }


async def get_leaderboard(limit=25):
    await ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, balance, wins, losses FROM users ORDER BY balance DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()

    results = []
    for user_id, balance, wins, losses in rows:
        username = f"User {user_id}"
        if bot_instance:
            user = bot_instance.get_user(user_id)
            if user:
                username = user.display_name or user.name
            else:
                try:
                    user = await bot_instance.fetch_user(user_id)
                    username = user.display_name or user.name
                except Exception:
                    pass
        results.append(
            {"user_id": user_id, "username": username, "balance": balance, "wins": wins, "losses": losses}
        )
    return results


async def get_guild_settings(guild_id: int):
    await ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT prefix, gambling_enabled, trivia_enabled FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"prefix": row[0], "gambling_enabled": bool(row[1]), "trivia_enabled": bool(row[2])}
            return {"prefix": "v!", "gambling_enabled": True, "trivia_enabled": True}


async def save_guild_settings(guild_id: int, prefix: str, gambling: bool, trivia: bool):
    await ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, prefix, gambling_enabled, trivia_enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET
                 prefix = excluded.prefix,
                 gambling_enabled = excluded.gambling_enabled,
                 trivia_enabled = excluded.trivia_enabled""",
            (guild_id, prefix, int(gambling), int(trivia)),
        )
        await db.commit()


def get_current_user(request: Request) -> Optional[dict]:
    return request.session.get("user")


def render(request: Request, name: str, context: dict):
    """TemplateResponse with correct arg order for modern Starlette/FastAPI."""
    return templates.TemplateResponse(request, name, context)


@app.exception_handler(Exception)
async def global_error(request: Request, exc: Exception):
    import traceback

    tb = traceback.format_exc()
    print(f"Dashboard error: {exc}\n{tb}")
    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#1a0b2e;color:#fff;padding:2rem">
    <h1>Dashboard error</h1>
    <pre style="background:#000;padding:1rem;overflow:auto">{exc}</pre>
    <p><a href="/" style="color:#c084fc">Back home</a></p>
    </body></html>"""
    return HTMLResponse(html, status_code=500)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    stats = await get_stats()
    top = await get_leaderboard(5)
    return render(
        request,
        "index.html",
        {"user": get_current_user(request), "stats": stats, "top_users": top},
    )


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request):
    users = await get_leaderboard(50)
    return render(
        request,
        "leaderboard.html",
        {"user": get_current_user(request), "users": users},
    )


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    stats = await get_stats()
    return render(
        request,
        "stats.html",
        {"user": get_current_user(request), "stats": stats},
    )


@app.get("/login")
async def login():
    if not CLIENT_ID:
        raise HTTPException(500, "DISCORD_CLIENT_ID not set in environment variables")
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    }
    return RedirectResponse(f"https://discord.com/api/oauth2/authorize?{urlencode(params)}")


@app.get("/callback")
async def callback(request: Request, code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse("/?error=auth")
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            f"{API_BASE}/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code != 200:
            return RedirectResponse("/?error=token")
        access_token = token_res.json()["access_token"]

        user_res = await client.get(
            f"{API_BASE}/users/@me", headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_res.json()

        guilds_res = await client.get(
            f"{API_BASE}/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"}
        )
        guilds_data = guilds_res.json() if guilds_res.status_code == 200 else []

    manageable = []
    for g in guilds_data:
        if not isinstance(g, dict):
            continue
        perms = int(g.get("permissions", 0))
        if (perms & 0x8) or (perms & 0x20):
            manageable.append(
                {
                    "id": str(g.get("id", "")),
                    "name": g.get("name", "Unknown"),
                    "icon": g.get("icon"),
                }
            )

    request.session["user"] = {
        "id": str(user_data["id"]),
        "username": user_data["username"],
        "avatar": user_data.get("avatar"),
        "guilds": manageable,
    }
    return RedirectResponse("/servers")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    bot_guild_ids = set()
    if bot_instance:
        bot_guild_ids = {str(g.id) for g in bot_instance.guilds}

    servers = []
    for g in user.get("guilds", []):
        if str(g.get("id", "")) in bot_guild_ids:
            servers.append({"id": g["id"], "name": g["name"], "icon": g.get("icon")})

    return render(
        request,
        "servers.html",
        {"user": user, "servers": servers, "is_owner": is_owner(user)},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_money_page(request: Request, money_msg: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    if not is_owner(user):
        raise HTTPException(403, "Only bot owners can use this page. Set BOT_OWNER_IDS.")

    return render(
        request,
        "admin.html",
        {"user": user, "money_msg": money_msg},
    )


@app.post("/admin/givemoney")
async def admin_give_money(
    request: Request,
    user_id: str = Form(...),
    amount: int = Form(...),
):
    user = get_current_user(request)
    if not user or not is_owner(user):
        raise HTTPException(403, "Owners only")

    try:
        target_id = int(user_id.strip())
    except ValueError:
        raise HTTPException(400, "Invalid user ID")
    if amount == 0:
        raise HTTPException(400, "Amount cannot be 0")

    await ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cur:
            row = await cur.fetchone()
            if row is None:
                await db.execute("INSERT INTO users (user_id, balance) VALUES (?, 100)", (target_id,))
                await db.commit()
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cur:
            new_bal = (await cur.fetchone())[0]

    action = "Gave" if amount > 0 else "Removed"
    msg = quote(f"✅ {action} {abs(amount):,} vibes. New balance: {new_bal:,}")
    return RedirectResponse(f"/admin?money_msg={msg}", status_code=303)


@app.get("/servers/{guild_id}", response_class=HTMLResponse)
async def server_page(request: Request, guild_id: str, saved: int = 0, money_msg: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    user_guild_ids = {str(g.get("id", "")) for g in user.get("guilds", [])}
    if guild_id not in user_guild_ids and not is_owner(user):
        raise HTTPException(403, "You don't manage this server")

    guild = None
    if bot_instance:
        g = bot_instance.get_guild(int(guild_id))
        if g:
            guild = {
                "id": str(g.id),
                "name": g.name,
                "icon": g.icon.key if g.icon else None,
                "member_count": g.member_count,
            }

    if not guild:
        for g in user.get("guilds", []):
            if str(g.get("id", "")) == guild_id:
                guild = {
                    "id": g["id"],
                    "name": g["name"],
                    "icon": g.get("icon"),
                    "member_count": None,
                }
                break

    if not guild:
        raise HTTPException(404, "Server not found or bot not in it")

    settings = await get_guild_settings(int(guild_id))
    return render(
        request,
        "server.html",
        {
            "user": user,
            "guild": guild,
            "settings": settings,
            "saved": bool(saved),
            "money_msg": money_msg,
            "is_owner": is_owner(user),
        },
    )


@app.post("/servers/{guild_id}/settings")
async def save_settings(
    request: Request,
    guild_id: str,
    prefix: str = Form("v!"),
    gambling_enabled: Optional[str] = Form(None),
    trivia_enabled: Optional[str] = Form(None),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    user_guild_ids = {str(g.get("id", "")) for g in user.get("guilds", [])}
    if guild_id not in user_guild_ids and not is_owner(user):
        raise HTTPException(403, "You don't manage this server")

    prefix = prefix.strip()[:10] or "v!"
    await save_guild_settings(int(guild_id), prefix, gambling_enabled == "1", trivia_enabled == "1")
    return RedirectResponse(f"/servers/{guild_id}?saved=1", status_code=303)


@app.post("/servers/{guild_id}/givemoney")
async def give_money(
    request: Request,
    guild_id: str,
    user_id: str = Form(...),
    amount: int = Form(...),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    user_guild_ids = {str(g.get("id", "")) for g in user.get("guilds", [])}
    if guild_id not in user_guild_ids and not is_owner(user):
        raise HTTPException(403, "You don't manage this server")

    try:
        target_id = int(user_id.strip())
    except ValueError:
        raise HTTPException(400, "Invalid user ID")
    if amount == 0:
        raise HTTPException(400, "Amount cannot be 0")

    await ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cur:
            row = await cur.fetchone()
            if row is None:
                await db.execute("INSERT INTO users (user_id, balance) VALUES (?, 100)", (target_id,))
                await db.commit()
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as cur:
            new_bal = (await cur.fetchone())[0]

    action = "Gave" if amount > 0 else "Removed"
    msg = quote(f"✅ {action} {abs(amount):,} vibes. New balance: {new_bal:,}")
    return RedirectResponse(f"/servers/{guild_id}?money_msg={msg}", status_code=303)
