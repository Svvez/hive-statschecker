"""
bot.py  —  Sniper 3.0  |  Hive Bedrock Stats Bot
Melhorias vs v2:
  - Daily / Weekly stat tracking via snapshots locais
  - Activity API (ETag) para polling inteligente — reduz rate limit drasticamente
  - sky-classic e sky-kits
  - Parkour (/parkour)
  - /costume comando dedicado (Catalogue API)
  - /position — rank global estimado
  - Campos extra: equipped_hat, SG (teleporters/launchpads/flares)
  - Modern leaderboard header (X-Hive-Leaderboard-Source: modern)
  - Exponential backoff em erros 429
  - Gamemode mismatch corrigido entre bot.py e card_generator.py
  - Comandos individuais por gamemode (/bedwars, /skywars, etc.)
  - Timeframe weekly + daily no /stats
"""

import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp
import asyncio
import sqlite3
import json
import os
import io
from datetime import datetime, timezone
from urllib.parse import quote

from card_generator import generate_stats_card, generate_profile_card, generate_lb_card

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID     = int(os.environ.get("CHANNEL_ID", "0"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "120"))  # aumentado para poupar rate limit
API_KEY        = os.environ.get("HIVE_API_KEY", "")
MAX_PLAYERS    = 10
LB_INTERVAL    = 300
API_BASE       = "https://api.playhive.com/v0"

# ── Colors ────────────────────────────────────────────────────────────────────
C_WIN    = 0x00E676
C_UPDATE = 0x2C2F33
C_LB     = 0xE8B84B
C_INFO   = 0x23272A
C_ERROR  = 0x992D22
C_PURPLE = 0x7C4DFF
C_CYAN   = 0x00E5FF

# ── Gamemodes ─────────────────────────────────────────────────────────────────
GAMEMODES = {
    "bed": {
        "name": "BedWars",
        "api":  "bed",
        "fields": ["played", "victories", "losses", "kills", "deaths",
                   "finals", "beds_destroyed", "win_streak"],
        "has_monthly": True,
        "has_seasons": True,
    },
    "wars": {
        "name": "Treasure Wars",
        "api":  "wars",
        "fields": ["played", "victories", "losses", "kills", "deaths",
                   "finals", "treasures_destroyed", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "sky": {
        "name": "Sky Wars",
        "api":  "sky",
        "fields": ["played", "victories", "losses", "kills", "deaths", "win_streak"],
        "has_monthly": False,
        "has_seasons": False,
    },
    "sky-classic": {
        "name": "Sky Wars Classic",
        "api":  "sky-classic",
        "fields": ["played", "victories", "losses", "kills", "deaths", "win_streak"],
        "has_monthly": False,
        "has_seasons": False,
    },
    "sky-kits": {
        "name": "Sky Wars Kits",
        "api":  "sky-kits",
        "fields": ["played", "victories", "losses", "kills", "deaths", "win_streak"],
        "has_monthly": False,
        "has_seasons": False,
    },
    "murder": {
        "name": "Murder Mystery",
        "api":  "murder",
        "fields": ["played", "victories", "losses", "kills", "deaths", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "ground": {
        "name": "Ground Wars",
        "api":  "ground",
        "fields": ["played", "victories", "losses", "kills", "deaths", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "build": {
        "name": "Just Build",
        "api":  "build",
        "fields": ["played", "victories", "losses"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "ctf": {
        "name": "Capture the Flag",
        "api":  "ctf",
        "fields": ["played", "victories", "losses", "kills", "deaths",
                   "flags_captured", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "dr": {
        "name": "Deathrun",
        "api":  "dr",
        "fields": ["played", "victories", "losses", "deaths", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "hide": {
        "name": "Hide & Seek",
        "api":  "hide",
        "fields": ["played", "victories", "losses", "deaths",
                   "hider_kills", "seeker_kills", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "drop": {
        "name": "Block Drop",
        "api":  "drop",
        "fields": ["played", "victories", "losses", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "bridge": {
        "name": "The Bridge",
        "api":  "bridge",
        "fields": ["played", "victories", "losses", "kills", "deaths",
                   "goals", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "party": {
        "name": "Party Games",
        "api":  "party",
        "fields": ["played", "victories", "losses", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "sg": {
        "name": "Survival Games",
        "api":  "sg",
        "fields": ["played", "victories", "losses", "kills", "deaths",
                   "teleporters_used", "launchpads_used", "flares_used", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
    "grav": {
        "name": "Gravity",
        "api":  "grav",
        "fields": ["played", "victories", "losses", "win_streak"],
        "has_monthly": True,
        "has_seasons": False,
    },
}

FIELD_LABELS = {
    "played":               "Games Played",
    "victories":            "Wins",
    "losses":               "Losses",
    "kills":                "Kills",
    "deaths":               "Deaths",
    "finals":               "Final Kills",
    "beds_destroyed":       "Beds Broken",
    "treasures_destroyed":  "Treasures",
    "win_streak":           "Win Streak",
    "flags_captured":       "Flags Captured",
    "hider_kills":          "Hider Kills",
    "seeker_kills":         "Seeker Kills",
    "goals":                "Goals",
    "teleporters_used":     "Teleporters",
    "launchpads_used":      "Launchpads",
    "flares_used":          "Flares",
}

GAMEMODE_CHOICES = [
    app_commands.Choice(name=v["name"], value=k)
    for k, v in GAMEMODES.items()
]

TIMEFRAME_CHOICES = [
    app_commands.Choice(name="All-Time", value="alltime"),
    app_commands.Choice(name="Monthly",  value="monthly"),
    app_commands.Choice(name="Weekly",   value="weekly"),
    app_commands.Choice(name="Daily",    value="daily"),
]

# ── Database ──────────────────────────────────────────────────────────────────
conn   = sqlite3.connect("hive_tracker.db")
cursor = conn.cursor()
cursor.executescript("""
    CREATE TABLE IF NOT EXISTS stats (
        player TEXT, gamemode TEXT, data TEXT,
        PRIMARY KEY (player, gamemode)
    );
    CREATE TABLE IF NOT EXISTS snapshots (
        player TEXT, gamemode TEXT, period TEXT, data TEXT, taken_at TEXT,
        PRIMARY KEY (player, gamemode, period)
    );
    CREATE TABLE IF NOT EXISTS leaderboard_cache (
        gamemode TEXT PRIMARY KEY, data TEXT
    );
    CREATE TABLE IF NOT EXISTS tracked_players (
        player TEXT PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS linked_users (
        discord_id TEXT PRIMARY KEY, player TEXT
    );
    CREATE TABLE IF NOT EXISTS activity_etags (
        player TEXT PRIMARY KEY, etag TEXT, uuid TEXT
    );
""")
conn.commit()

def load_tracked_players():
    cursor.execute("SELECT player FROM tracked_players")
    rows = cursor.fetchall()
    if rows:
        return [r[0] for r in rows]
    seed = [p.strip() for p in os.environ.get("TRACKED_PLAYERS", "").split(",") if p.strip()]
    for p in seed:
        cursor.execute("INSERT OR IGNORE INTO tracked_players (player) VALUES (?)", (p.lower(),))
    conn.commit()
    return seed

def db_add_player(player):
    cursor.execute("INSERT OR IGNORE INTO tracked_players (player) VALUES (?)", (player.lower(),))
    conn.commit()

def db_remove_player(player):
    cursor.execute("DELETE FROM tracked_players WHERE player=?", (player.lower(),))
    conn.commit()

def db_link_user(discord_id, player):
    cursor.execute("REPLACE INTO linked_users (discord_id, player) VALUES (?,?)",
                   (str(discord_id), player.lower()))
    conn.commit()

def db_unlink_user(discord_id):
    cursor.execute("DELETE FROM linked_users WHERE discord_id=?", (str(discord_id),))
    conn.commit()

def db_get_linked(discord_id):
    cursor.execute("SELECT player FROM linked_users WHERE discord_id=?", (str(discord_id),))
    row = cursor.fetchone()
    return row[0] if row else None

def get_stored(player, gamemode):
    cursor.execute("SELECT data FROM stats WHERE player=? AND gamemode=?",
                   (player.lower(), gamemode))
    row = cursor.fetchone()
    return json.loads(row[0]) if row else None

def save_stats(player, gamemode, data):
    cursor.execute("REPLACE INTO stats (player, gamemode, data) VALUES (?,?,?)",
                   (player.lower(), gamemode, json.dumps(data)))
    conn.commit()

def get_snapshot(player, gamemode, period):
    cursor.execute("SELECT data FROM snapshots WHERE player=? AND gamemode=? AND period=?",
                   (player.lower(), gamemode, period))
    row = cursor.fetchone()
    return json.loads(row[0]) if row else None

def save_snapshot(player, gamemode, period, data):
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "REPLACE INTO snapshots (player, gamemode, period, data, taken_at) VALUES (?,?,?,?,?)",
        (player.lower(), gamemode, period, json.dumps(data), now)
    )
    conn.commit()

def get_lb_cache(gamemode):
    cursor.execute("SELECT data FROM leaderboard_cache WHERE gamemode=?", (gamemode,))
    row = cursor.fetchone()
    return json.loads(row[0]) if row else []

def save_lb_cache(gamemode, data):
    cursor.execute("REPLACE INTO leaderboard_cache (gamemode, data) VALUES (?,?)",
                   (gamemode, json.dumps(data)))
    conn.commit()

def get_etag(player):
    cursor.execute("SELECT etag, uuid FROM activity_etags WHERE player=?", (player.lower(),))
    row = cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)

def save_etag(player, etag, uuid):
    cursor.execute("REPLACE INTO activity_etags (player, etag, uuid) VALUES (?,?,?)",
                   (player.lower(), etag, uuid))
    conn.commit()

TRACKED_PLAYERS: list[str] = load_tracked_players()

# ── API helpers ───────────────────────────────────────────────────────────────
def _headers(extra=None):
    h = {
        "Content-Type": "application/json",
        "X-Hive-Leaderboard-Source": "modern",  # modern leaderboard source
    }
    if API_KEY:
        h["Authorization"] = API_KEY
    if extra:
        h.update(extra)
    return h

async def _get(session, url, retries=3, extra_headers=None):
    """GET com exponential backoff em 429."""
    for attempt in range(retries):
        try:
            async with session.get(
                url,
                headers=_headers(extra_headers),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    return await r.json(), dict(r.headers)
                if r.status == 429:
                    wait = 2 ** attempt * 5
                    print(f"[RATE LIMIT] {url} — aguardar {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if r.status == 304:
                    return None, {"__not_modified": True}
                return None, {}
        except Exception as e:
            print(f"[ERROR] GET {url}: {e}")
            await asyncio.sleep(2 ** attempt)
    return None, {}

async def _get_data(session, url, retries=3):
    data, _ = await _get(session, url, retries=retries)
    return data

# ── API: Player ───────────────────────────────────────────────────────────────
async def fetch_profile(session, player):
    return await _get_data(session, f"{API_BASE}/player/{quote(player, safe='')}")

async def fetch_player_search(session, partial):
    return await _get_data(session, f"{API_BASE}/player/search/{quote(partial, safe='')}")

async def fetch_all_games(session, player):
    return await _get_data(session, f"{API_BASE}/game/all/all/{quote(player, safe='')}")

async def fetch_main_stats(session, player):
    return await _get_data(session, f"{API_BASE}/game/all/main/{quote(player, safe='')}")

async def fetch_activity(session, player_uuid, etag=None):
    """Activity API com suporte a ETag — não gasta rate limit se não houve alteração."""
    extra = {"If-None-Match": etag} if etag else None
    data, headers = await _get(
        session,
        f"{API_BASE}/player/activity/{quote(player_uuid, safe='')}",
        extra_headers=extra
    )
    if headers.get("__not_modified"):
        return None, etag  # sem alteração
    new_etag = headers.get("ETag") or headers.get("etag")
    return data, new_etag

# ── API: Stats ────────────────────────────────────────────────────────────────
async def fetch_stats(session, player, gamemode, timeframe="alltime", year=None, month=None):
    api_key = GAMEMODES[gamemode]["api"]
    enc     = quote(player, safe="")
    if timeframe == "alltime":
        url = f"{API_BASE}/game/all/{api_key}/{enc}"
    elif timeframe == "monthly":
        if year and month:
            url = f"{API_BASE}/game/monthly/player/{api_key}/{enc}/{year}/{month}"
        else:
            url = f"{API_BASE}/game/monthly/player/{api_key}/{enc}"
    else:
        url = f"{API_BASE}/game/all/{api_key}/{enc}"
    data = await _get_data(session, url)
    if isinstance(data, list):
        return data[0] if data else None
    return data

# ── API: Parkour ──────────────────────────────────────────────────────────────
async def fetch_parkour(session, player):
    return await _get_data(session, f"{API_BASE}/game/all/parkour/{quote(player, safe='')}")

# ── API: Leaderboards ─────────────────────────────────────────────────────────
async def fetch_leaderboard(session, gamemode, amount=10, skip=0):
    api_key = GAMEMODES[gamemode]["api"]
    return await _get_data(session,
        f"{API_BASE}/game/all/{api_key}?amount={amount}&skip={skip}")

async def fetch_monthly_lb(session, gamemode, amount=50, year=None, month=None):
    if not GAMEMODES[gamemode].get("has_monthly"):
        return None
    api_key = GAMEMODES[gamemode]["api"]
    if year and month:
        url = f"{API_BASE}/game/monthly/{api_key}/{year}/{month}/{amount}/0"
    else:
        url = f"{API_BASE}/game/monthly/{api_key}"
    return await _get_data(session, url)

async def fetch_available_monthly(session, gamemode):
    if not GAMEMODES[gamemode].get("has_monthly"):
        return None
    api_key = GAMEMODES[gamemode]["api"]
    return await _get_data(session, f"{API_BASE}/game/monthly/{api_key}/available")

async def fetch_season_lb(session, season=1, amount=50, skip=0):
    return await _get_data(session,
        f"{API_BASE}/game/season/bed/{season}/{amount}/{skip}")

async def fetch_season_player(session, player, season=1):
    enc = quote(player, safe="")
    return await _get_data(session, f"{API_BASE}/game/season/player/bed/{enc}/{season}")

# ── API: Global & Meta ────────────────────────────────────────────────────────
async def fetch_global_stats(session):
    return await _get_data(session, f"{API_BASE}/global/statistics")

async def fetch_game_maps(session, gamemode):
    api_key = GAMEMODES[gamemode]["api"]
    return await _get_data(session, f"{API_BASE}/game/map/{api_key}")

async def fetch_game_meta(session, gamemode):
    api_key = GAMEMODES[gamemode]["api"]
    return await _get_data(session, f"{API_BASE}/game/meta/{api_key}")

# ── API: Catalogue ────────────────────────────────────────────────────────────
async def fetch_costume_catalogue(session, limit=50, offset=0):
    return await _get_data(session,
        f"{API_BASE}/catalogue/costumes?limit={limit}&offset={offset}")

async def fetch_title_catalogue(session, limit=50, offset=0):
    return await _get_data(session,
        f"{API_BASE}/catalogue/titles?limit={limit}&offset={offset}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def sg(data, key):
    return data.get(key, 0) if data else 0

def kdr(data):
    k, d = sg(data, "kills"), sg(data, "deaths")
    return round(k / max(d, 1), 2) if (k or d) else None

def win_rate(data):
    w, p = sg(data, "victories"), sg(data, "played")
    return round((w / max(p, 1)) * 100, 1) if p else 0

def ts():
    return datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")

def diff_str(d):
    return f"(+{d})" if d > 0 else f"({d})" if d < 0 else "(+0)"

def resolve_player(interaction, player_arg):
    if player_arg:
        return player_arg
    return db_get_linked(interaction.user.id)

def fmt(n):
    if isinstance(n, float):
        return f"{n:,.2f}"
    if isinstance(n, int):
        return f"{n:,}"
    return str(n)

def get_period_data(player, gamemode, timeframe, new_data):
    """
    Para weekly/daily, devolve (current, snapshot_as_old).
    Para alltime/monthly, devolve (new_data, stored_data).
    """
    if timeframe in ("weekly", "daily"):
        snap = get_snapshot(player, gamemode, timeframe)
        return new_data, snap  # snap pode ser None se não existir ainda
    elif timeframe == "alltime":
        return new_data, get_stored(player, gamemode)
    return new_data, None

# ── Embed builders ────────────────────────────────────────────────────────────
def build_notification_embed(player, gamemode, new, old):
    gm        = GAMEMODES[gamemode]
    wins_diff = sg(new, "victories") - sg(old, "victories")
    embed = discord.Embed(
        title=f"📊 {player}  ·  {gm['name']}",
        color=C_WIN if wins_diff > 0 else C_UPDATE,
        timestamp=datetime.now(timezone.utc)
    )
    if wins_diff > 0:
        embed.description = f"**+{wins_diff} {'win' if wins_diff == 1 else 'wins'}**"
    lines = []
    for field in gm["fields"]:
        label       = FIELD_LABELS.get(field, field.replace("_", " ").title())
        nv, ov      = sg(new, field), sg(old, field)
        d           = nv - ov
        marker      = f"  `{diff_str(d)}`" if d != 0 else ""
        lines.append(f"`{label:<20}` {fmt(nv)}{marker}")
    k_new = kdr(new)
    if k_new is not None:
        k_old_v = kdr(old) or 0
        d       = round(k_new - k_old_v, 2)
        marker  = f"  `{diff_str(d)}`" if d != 0 else ""
        lines.append(f"`{'K/D Ratio':<20}` {k_new}{marker}")
    embed.add_field(name="Stats", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    return embed

def _build_top10_embed(gamemode, new_lb, old_lb):
    gm        = GAMEMODES[gamemode]
    old_names = {e.get("human_index", e.get("username", "")).lower() for e in old_lb}
    lines     = []
    for i, entry in enumerate(new_lb):
        name   = entry.get("human_index", entry.get("username", "Unknown"))
        streak = sg(entry, "win_streak")
        wins   = sg(entry, "victories")
        badge  = "  ← **new**" if name.lower() not in old_names else ""
        lines.append(f"`#{i+1:02}`  `{name:<20}`  {streak} streak  ·  {wins:,} wins{badge}")
    embed = discord.Embed(
        title=f"{gm['name']}  ·  Top 10 Win Streak",
        description="\n".join(lines),
        color=C_LB, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    return embed

# ── Bot setup ─────────────────────────────────────────────────────────────────
_session = None
intents  = discord.Intents.default()
intents.message_content = True
bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ── Snapshot loop ─────────────────────────────────────────────────────────────
@tasks.loop(minutes=1)
async def snapshot_loop():
    """Tira snapshots diários (00:00 UTC) e semanais (segunda 00:00 UTC)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    now = datetime.now(timezone.utc)
    periods = []
    if now.hour == 0 and now.minute == 0:
        periods.append("daily")
        if now.weekday() == 0:
            periods.append("weekly")
    if not periods:
        return
    print(f"[SNAPSHOT] A tirar snapshots: {periods}")
    for player in list(TRACKED_PLAYERS):
        for gamemode in GAMEMODES:
            data = await fetch_stats(_session, player, gamemode)
            if data:
                for period in periods:
                    save_snapshot(player, gamemode, period, data)
            await asyncio.sleep(0.5)

# ── Background loops ──────────────────────────────────────────────────────────
@tasks.loop(seconds=CHECK_INTERVAL)
async def track_loop():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
    for player in list(TRACKED_PLAYERS):
        # Usar Activity API para ver se o jogador jogou antes de pedir tudo
        profile = await fetch_profile(_session, player)
        uuid    = profile.get("UUID") if profile else None

        player_active = True  # default: verificar sempre se não temos UUID
        if uuid:
            etag, stored_uuid = get_etag(player)
            # se o UUID mudou (gamertag mudou), reset etag
            if stored_uuid != uuid:
                etag = None
            activity, new_etag = await fetch_activity(_session, uuid, etag)
            if new_etag:
                save_etag(player, new_etag, uuid)
            if activity is None and etag:
                # 304 Not Modified — jogador não jogou nada novo
                player_active = False

        if not player_active:
            await asyncio.sleep(0.3)
            continue

        for gamemode in GAMEMODES:
            new = await fetch_stats(_session, player, gamemode)
            if not new:
                await asyncio.sleep(0.4)
                continue
            old = get_stored(player, gamemode)
            if old is None:
                save_stats(player, gamemode, new)
                await asyncio.sleep(0.4)
                continue
            changed = [f for f in GAMEMODES[gamemode]["fields"] if sg(new, f) != sg(old, f)]
            if changed:
                save_stats(player, gamemode, new)
                try:
                    monthly = await fetch_stats(_session, player, gamemode, "monthly")
                    img_bytes = await asyncio.get_event_loop().run_in_executor(
                        None, generate_stats_card, player, gamemode, new, old, monthly
                    )
                    file  = discord.File(fp=io.BytesIO(img_bytes), filename="stats.png")
                    embed = discord.Embed(
                        color=C_WIN if sg(new, "victories") > sg(old, "victories") else C_UPDATE,
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.set_image(url="attachment://stats.png")
                    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
                    await channel.send(file=file, embed=embed)
                except Exception:
                    await channel.send(embed=build_notification_embed(player, gamemode, new, old))
            await asyncio.sleep(0.4)

@tasks.loop(seconds=LB_INTERVAL)
async def leaderboard_loop():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
    for gamemode in GAMEMODES:
        new_lb = await fetch_leaderboard(_session, gamemode, amount=10)
        if not new_lb or not isinstance(new_lb, list):
            await asyncio.sleep(1)
            continue
        old_lb     = get_lb_cache(gamemode)
        old_names  = {e.get("human_index", e.get("username", "")).lower() for e in old_lb}
        new_entries = [e for e in new_lb
                       if e.get("human_index", e.get("username", "")).lower() not in old_names]
        if new_entries:
            save_lb_cache(gamemode, new_lb)
            try:
                await channel.send(embed=_build_top10_embed(gamemode, new_lb, old_lb))
            except Exception as e:
                print(f"[ERROR] LB send: {e}")
        await asyncio.sleep(1)

# ═════════════════════════════════════════════════════════════════════════════
#  HELPER: enviar card de stats
# ═════════════════════════════════════════════════════════════════════════════
async def send_stats_card(interaction, player, gm, timeframe="alltime", year=None, month=None):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        if timeframe in ("weekly", "daily"):
            new = await fetch_stats(s, player, gm, "alltime")
        elif timeframe == "monthly":
            new = await fetch_stats(s, player, gm, "monthly", year=year, month=month)
        else:
            new = await fetch_stats(s, player, gm, "alltime")

    if not new:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Jogador **{player}** não encontrado no Hive.", color=C_ERROR))
        return

    current, old = get_period_data(player, gm, timeframe, new)

    # para alltime, monthly: monthly sidebar
    monthly = None
    if timeframe == "alltime":
        async with aiohttp.ClientSession() as s:
            monthly = await fetch_stats(s, player, gm, "monthly")

    period_label = {
        "alltime": "ALL-TIME",
        "monthly": "MONTHLY",
        "weekly":  "WEEKLY",
        "daily":   "DAILY",
    }.get(timeframe, timeframe.upper())

    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_stats_card, player, gm, current, old if old else current, monthly
        )
        file  = discord.File(fp=io.BytesIO(img_bytes), filename="stats.png")
        embed = discord.Embed(color=0x0d1018, timestamp=datetime.now(timezone.utc))
        embed.set_image(url="attachment://stats.png")
        embed.set_footer(text=f"Sniper 3.0  ·  {period_label}  ·  {ts()}")
        await interaction.followup.send(file=file, embed=embed)
    except Exception as e:
        print(f"[ERROR] card gen: {e}")
        gm_info = GAMEMODES[gm]
        lines   = [f"`{FIELD_LABELS.get(f, f):<20}` {fmt(sg(current, f))}"
                   for f in gm_info["fields"]]
        kd = kdr(current)
        if kd:
            lines.append(f"`{'K/D Ratio':<20}` {kd}")
        lines.append(f"`{'Win Rate':<20}` {win_rate(current)}%")
        await interaction.followup.send(embed=discord.Embed(
            title=f"{player}  ·  {gm_info['name']}  ·  {period_label}",
            description="\n".join(lines), color=C_INFO))

# ═════════════════════════════════════════════════════════════════════════════
#  SLASH COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

# ── /stats ────────────────────────────────────────────────────────────────────
@tree.command(name="stats", description="Ver stats de um jogador (suporta daily/weekly/monthly)")
@app_commands.describe(
    player="Gamertag Xbox (opcional se tiveres conta ligada)",
    gamemode="Modo de jogo (padrão: bed)",
    timeframe="Período de tempo"
)
@app_commands.choices(gamemode=GAMEMODE_CHOICES, timeframe=TIMEFRAME_CHOICES)
async def cmd_stats(interaction: discord.Interaction,
                    player:    str = None,
                    gamemode:  app_commands.Choice[str] = None,
                    timeframe: app_commands.Choice[str] = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga a tua conta com `/link`.",
            color=C_ERROR), ephemeral=True)
        return
    gm = gamemode.value if gamemode else "bed"
    tf = timeframe.value if timeframe else "alltime"
    await send_stats_card(interaction, player, gm, tf)

# ── Comandos individuais por gamemode (como o AssistantMC) ────────────────────
def make_gamemode_cmd(gm_key, gm_name):
    @app_commands.describe(
        player="Gamertag Xbox (opcional se tiveres conta ligada)",
        timeframe="Período de tempo"
    )
    @app_commands.choices(timeframe=TIMEFRAME_CHOICES)
    async def _cmd(interaction: discord.Interaction,
                   player: str = None,
                   timeframe: app_commands.Choice[str] = None):
        p = resolve_player(interaction, player)
        if not p:
            await interaction.response.send_message(embed=discord.Embed(
                description="Fornece um username ou liga com `/link`.", color=C_ERROR),
                ephemeral=True)
            return
        tf = timeframe.value if timeframe else "alltime"
        await send_stats_card(interaction, p, gm_key, tf)
    _cmd.__name__ = gm_key.replace("-", "_")
    return _cmd

# Registar comandos individuais
_gm_cmds = {
    "bedwars":       "bed",
    "treasurewars":  "wars",
    "skywars":       "sky",
    "skywars_classic": "sky-classic",
    "skywars_kits":  "sky-kits",
    "murdermystery": "murder",
    "groundwars":    "ground",
    "justbuild":     "build",
    "capturetheflag":"ctf",
    "deathrun":      "dr",
    "hideandseek":   "hide",
    "blockdrop":     "drop",
    "bridge":        "bridge",
    "partygames":    "party",
    "survivalgames": "sg",
    "gravity":       "grav",
}
for cmd_name, gm_key in _gm_cmds.items():
    fn = make_gamemode_cmd(gm_key, GAMEMODES[gm_key]["name"])
    tree.command(name=cmd_name, description=f"Stats de {GAMEMODES[gm_key]['name']}")(fn)

# ── /parkour ──────────────────────────────────────────────────────────────────
@tree.command(name="parkour", description="Ver stats de Parkour Worlds de um jogador")
@app_commands.describe(player="Gamertag Xbox (opcional se tiveres conta ligada)")
async def cmd_parkour(interaction: discord.Interaction, player: str = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_parkour(s, player)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Sem dados de Parkour para **{player}**.", color=C_ERROR))
        return
    parkours = data.get("parkours", {})
    if not parkours:
        await interaction.followup.send(embed=discord.Embed(
            description=f"**{player}** ainda não jogou nenhum Parkour.", color=C_INFO))
        return

    embed = discord.Embed(
        title=f"🏃 {player}  ·  Parkour Worlds",
        color=C_CYAN, timestamp=datetime.now(timezone.utc)
    )
    # Mostrar estrelas globais e por mundo
    global_stars = data.get("star_count", 0)
    embed.description = f"⭐ **{global_stars}** estrelas no total\n"

    for world_name, courses in list(parkours.items())[:5]:  # máx 5 mundos
        world_stars = sum(c.get("star_count", 0) for c in courses.values() if isinstance(c, dict))
        lines = []
        for course_name, cdata in list(courses.items())[:8]:
            if not isinstance(cdata, dict):
                continue
            best = cdata.get("best_run_time")
            stars = cdata.get("star_count", 0)
            if best:
                secs = best / 20  # ticks → segundos (20 TPS)
                time_str = f"{secs:.2f}s"
            else:
                time_str = "—"
            lines.append(f"`{course_name[:20]:<20}` {time_str}  ⭐{stars}")
        if lines:
            embed.add_field(
                name=f"🌍 {world_name}  (⭐{world_stars})",
                value="\n".join(lines),
                inline=False
            )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /costume ──────────────────────────────────────────────────────────────────
@tree.command(name="costume", description="Ver o costume equipado de um jogador")
@app_commands.describe(player="Gamertag Xbox (opcional se tiveres conta ligada)")
async def cmd_costume(interaction: discord.Interaction, player: str = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        profile = await fetch_profile(s, player)
        main    = await fetch_main_stats(s, player)
    if not profile:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Jogador **{player}** não encontrado.", color=C_ERROR))
        return

    costume   = profile.get("equipped_hub_title", "Nenhum")
    hat       = main.get("equipped_hat") if main else None
    avatar    = profile.get("avatar", None)
    costume_name = costume if isinstance(costume, str) else costume.get("title", "Nenhum") if isinstance(costume, dict) else "Nenhum"

    embed = discord.Embed(
        title=f"🎭 {player}  ·  Cosméticos",
        color=C_PURPLE, timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Hub Title",  value=costume_name or "Nenhum", inline=True)
    embed.add_field(name="Hat",        value=str(hat) if hat else "Nenhum", inline=True)
    embed.add_field(name="Hive+",      value="✅" if profile.get("paid_rank") else "❌", inline=True)
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /position ─────────────────────────────────────────────────────────────────
@tree.command(name="position", description="Rank estimado de um jogador em cada modo")
@app_commands.describe(
    player="Gamertag Xbox (opcional se tiveres conta ligada)",
    gamemode="Modo de jogo específico (opcional)"
)
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_position(interaction: discord.Interaction,
                       player: str = None,
                       gamemode: app_commands.Choice[str] = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()

    target_modes = [gamemode.value] if gamemode else list(GAMEMODES.keys())

    async with aiohttp.ClientSession() as s:
        results = []
        for gm_key in target_modes:
            data = await fetch_stats(s, player, gm_key)
            if not data:
                continue
            wins = sg(data, "victories")
            if wins == 0:
                continue
            # Estimar posição: buscar top 100 e ver onde o jogador se encaixaria
            lb = await fetch_leaderboard(s, gm_key, amount=100)
            if lb and isinstance(lb, list):
                pos = next(
                    (i + 1 for i, e in enumerate(lb)
                     if e.get("human_index", e.get("username", "")).lower() == player.lower()),
                    None
                )
                # Se não está no top 100, estimar pela posição de wins
                if pos is None:
                    below = sum(1 for e in lb if sg(e, "victories") > wins)
                    pos = f"~{below + 1}+" if below < 100 else ">100"
                results.append((GAMEMODES[gm_key]["name"], pos, wins))
            await asyncio.sleep(0.3)

    if not results:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Sem dados suficientes para **{player}**.", color=C_ERROR))
        return

    embed = discord.Embed(
        title=f"🏅 {player}  ·  Posições Globais",
        color=C_LB, timestamp=datetime.now(timezone.utc)
    )
    lines = []
    for name, pos, wins in results:
        pos_str = f"**#{pos}**" if isinstance(pos, int) else f"`{pos}`"
        lines.append(f"`{name:<22}` {pos_str}  ·  {wins:,} wins")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /profile ──────────────────────────────────────────────────────────────────
@tree.command(name="profile", description="Ver perfil geral de um jogador no Hive")
@app_commands.describe(player="Gamertag Xbox (opcional se tiveres conta ligada)")
async def cmd_profile(interaction: discord.Interaction, player: str = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        profile = await fetch_profile(s, player)
        main    = await fetch_main_stats(s, player)
    if not profile:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Jogador **{player}** não encontrado.", color=C_ERROR))
        return
    # Adicionar equipped_hat ao profile para o card
    if main and main.get("equipped_hat"):
        profile["equipped_hat"] = main["equipped_hat"]
    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_profile_card, player, profile)
        file  = discord.File(fp=io.BytesIO(img_bytes), filename="profile.png")
        embed = discord.Embed(color=0x0d1018, timestamp=datetime.now(timezone.utc))
        embed.set_image(url="attachment://profile.png")
        embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
        await interaction.followup.send(file=file, embed=embed)
    except Exception as e:
        print(f"[ERROR] profile card: {e}")
        xp = profile.get("xp", 0)
        hat = profile.get("equipped_hat", "Nenhum")
        costume = profile.get("equipped_hub_title", "Nenhum")
        costume_name = costume if isinstance(costume, str) else costume.get("title", "Nenhum") if isinstance(costume, dict) else "Nenhum"
        await interaction.followup.send(embed=discord.Embed(
            title=f"Perfil — {player}",
            description=(
                f"**XP:** {xp:,}\n"
                f"**First seen:** {profile.get('first_played_hive', 'N/A')}\n"
                f"**Hive+:** {'Sim' if profile.get('paid_rank') else 'Não'}\n"
                f"**Hub Title:** {costume_name}\n"
                f"**Hat:** {hat}"
            ), color=C_INFO))

# ── /allstats ─────────────────────────────────────────────────────────────────
@tree.command(name="allstats", description="Resumo de todos os jogos de um jogador")
@app_commands.describe(player="Gamertag Xbox (opcional se tiveres conta ligada)")
async def cmd_allstats(interaction: discord.Interaction, player: str = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_all_games(s, player)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Jogador **{player}** não encontrado.", color=C_ERROR))
        return
    embed = discord.Embed(
        title=f"📊 {player}  ·  Todos os Jogos",
        color=C_PURPLE, timestamp=datetime.now(timezone.utc)
    )
    summary = []
    for gm_key, gm_info in GAMEMODES.items():
        api_key = gm_info["api"]
        gm_data = data.get(api_key)
        if not gm_data:
            continue
        wins   = sg(gm_data, "victories")
        played = sg(gm_data, "played")
        wr     = win_rate(gm_data)
        streak = sg(gm_data, "win_streak")
        line   = f"**{gm_info['name']}** — {wins:,} wins / {played:,} jogos ({wr}% WR)"
        if streak:
            line += f" | {streak} streak"
        summary.append(line)
    embed.description = "\n".join(summary) if summary else "Sem dados disponíveis."
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /compare ──────────────────────────────────────────────────────────────────
@tree.command(name="compare", description="Comparar dois jogadores lado a lado")
@app_commands.describe(player1="Primeiro jogador", player2="Segundo jogador",
                       gamemode="Modo de jogo", timeframe="Período")
@app_commands.choices(gamemode=GAMEMODE_CHOICES, timeframe=TIMEFRAME_CHOICES)
async def cmd_compare(interaction: discord.Interaction,
                      player1: str, player2: str,
                      gamemode: app_commands.Choice[str] = None,
                      timeframe: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bed"
    tf = timeframe.value if timeframe else "alltime"
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        d1, d2 = await asyncio.gather(
            fetch_stats(s, player1, gm),
            fetch_stats(s, player2, gm)
        )
    if not d1:
        await interaction.followup.send(embed=discord.Embed(
            description=f"**{player1}** não encontrado.", color=C_ERROR))
        return
    if not d2:
        await interaction.followup.send(embed=discord.Embed(
            description=f"**{player2}** não encontrado.", color=C_ERROR))
        return

    # Para weekly/daily, usar snapshots como base
    if tf in ("weekly", "daily"):
        snap1 = get_snapshot(player1, gm, tf)
        snap2 = get_snapshot(player2, gm, tf)
        if snap1:
            d1 = {k: sg(d1, k) - sg(snap1, k) for k in GAMEMODES[gm]["fields"]}
        if snap2:
            d2 = {k: sg(d2, k) - sg(snap2, k) for k in GAMEMODES[gm]["fields"]}

    gm_info = GAMEMODES[gm]
    period_label = {"alltime": "All-Time", "monthly": "Monthly", "weekly": "Weekly", "daily": "Daily"}.get(tf, tf)
    embed   = discord.Embed(
        title=f"⚔️  {player1}  vs  {player2}  ·  {gm_info['name']}  [{period_label}]",
        color=C_LB, timestamp=datetime.now(timezone.utc)
    )
    p1_lines, p2_lines, label_lines = [], [], []
    for field in gm_info["fields"]:
        label  = FIELD_LABELS.get(field, field.replace("_", " ").title())
        v1, v2 = sg(d1, field), sg(d2, field)
        b1     = "**" if v1 > v2 else ""
        b2     = "**" if v2 > v1 else ""
        p1_lines.append(f"{b1}{fmt(v1)}{b1}")
        p2_lines.append(f"{b2}{fmt(v2)}{b2}")
        label_lines.append(f"`{label}`")
    kd1, kd2 = kdr(d1), kdr(d2)
    if kd1 is not None or kd2 is not None:
        kd1 = kd1 or 0; kd2 = kd2 or 0
        b1 = "**" if kd1 > kd2 else ""
        b2 = "**" if kd2 > kd1 else ""
        p1_lines.append(f"{b1}{kd1}{b1}")
        p2_lines.append(f"{b2}{kd2}{b2}")
        label_lines.append("`K/D`")
    wr1, wr2 = win_rate(d1), win_rate(d2)
    b1 = "**" if wr1 > wr2 else ""
    b2 = "**" if wr2 > wr1 else ""
    p1_lines.append(f"{b1}{wr1}%{b1}")
    p2_lines.append(f"{b2}{wr2}%{b2}")
    label_lines.append("`Win Rate`")
    embed.add_field(name=player1,  value="\n".join(p1_lines),    inline=True)
    embed.add_field(name="Stat",   value="\n".join(label_lines), inline=True)
    embed.add_field(name=player2,  value="\n".join(p2_lines),    inline=True)
    embed.set_footer(text=f"Sniper 3.0  ·  **negrito** = mais alto  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /lb ───────────────────────────────────────────────────────────────────────
@tree.command(name="lb", description="Leaderboard global (Top 100, com paginação)")
@app_commands.describe(gamemode="Modo de jogo", amount="Nº de jogadores (máx 100)", page="Página")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_lb(interaction: discord.Interaction,
                 gamemode: app_commands.Choice[str] = None,
                 amount: int = 10,
                 page:   int = 1):
    gm     = gamemode.value if gamemode else "bed"
    amount = max(1, min(amount, 100))
    skip   = (page - 1) * amount
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        lb = await fetch_leaderboard(s, gm, amount=amount, skip=skip)
    if not lb:
        await interaction.followup.send(embed=discord.Embed(
            description="Não foi possível obter o leaderboard.", color=C_ERROR))
        return
    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_lb_card, gm, lb)
        file  = discord.File(fp=io.BytesIO(img_bytes), filename="lb.png")
        embed = discord.Embed(color=0x0d1018, timestamp=datetime.now(timezone.utc))
        embed.set_image(url="attachment://lb.png")
        embed.set_footer(text=f"Sniper 3.0  ·  Página {page}  ·  {ts()}")
        await interaction.followup.send(file=file, embed=embed)
    except Exception as e:
        print(f"[ERROR] lb card: {e}")
        lines = []
        for i, entry in enumerate(lb[:20]):
            name   = entry.get("human_index", entry.get("username", "?"))
            streak = sg(entry, "win_streak")
            wins   = sg(entry, "victories")
            lines.append(f"`#{skip+i+1:03}`  `{name:<20}`  {streak} streak · {wins:,} wins")
        embed = discord.Embed(
            title=f"{GAMEMODES[gm]['name']} · Top {amount} (pág. {page})",
            description="\n".join(lines), color=C_LB, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
        await interaction.followup.send(embed=embed)

# ── /monthlylb ────────────────────────────────────────────────────────────────
@tree.command(name="monthlylb", description="Leaderboard mensal de um modo")
@app_commands.describe(gamemode="Modo de jogo", year="Ano (vazio = atual)", month="Mês 1-12 (vazio = atual)")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_monthly_lb(interaction: discord.Interaction,
                         gamemode: app_commands.Choice[str] = None,
                         year:  int = None,
                         month: int = None):
    gm = gamemode.value if gamemode else "bed"
    if not GAMEMODES[gm].get("has_monthly"):
        await interaction.response.send_message(embed=discord.Embed(
            description=f"**{GAMEMODES[gm]['name']}** não tem leaderboard mensal.",
            color=C_ERROR), ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        lb = await fetch_monthly_lb(s, gm, amount=20, year=year, month=month)
    if not lb or not isinstance(lb, list):
        await interaction.followup.send(embed=discord.Embed(
            description="Sem dados para esse período.", color=C_ERROR))
        return
    period = f"{year}/{month:02d}" if year and month else "Mês Atual"
    lines  = []
    for i, entry in enumerate(lb[:20]):
        name   = entry.get("human_index", entry.get("username", "?"))
        wins   = sg(entry, "victories")
        streak = sg(entry, "win_streak")
        lines.append(f"`#{i+1:02}`  `{name:<20}`  {wins:,} wins · {streak} streak")
    embed = discord.Embed(
        title=f"{GAMEMODES[gm]['name']}  ·  Mensal  ·  {period}",
        description="\n".join(lines), color=C_CYAN, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /monthlyavailable ─────────────────────────────────────────────────────────
@tree.command(name="monthlyavailable", description="Ver que meses têm leaderboard disponível")
@app_commands.describe(gamemode="Modo de jogo")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_monthly_available(interaction: discord.Interaction,
                                gamemode: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bed"
    if not GAMEMODES[gm].get("has_monthly"):
        await interaction.response.send_message(embed=discord.Embed(
            description=f"**{GAMEMODES[gm]['name']}** não tem leaderboard mensal.",
            color=C_ERROR), ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_available_monthly(s, gm)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description="Sem dados disponíveis.", color=C_ERROR))
        return
    lines = [f"• {d}" for d in data] if isinstance(data, list) else [str(data)]
    embed = discord.Embed(
        title=f"{GAMEMODES[gm]['name']}  ·  Meses Disponíveis",
        description="\n".join(lines), color=C_INFO
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /monthlystats ─────────────────────────────────────────────────────────────
@tree.command(name="monthlystats", description="Stats mensais de um jogador")
@app_commands.describe(player="Gamertag Xbox", gamemode="Modo de jogo",
                       year="Ano (ex: 2025)", month="Mês 1-12")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_monthly_stats(interaction: discord.Interaction,
                             player:   str = None,
                             gamemode: app_commands.Choice[str] = None,
                             year:  int = None,
                             month: int = None):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    gm = gamemode.value if gamemode else "bed"
    if not GAMEMODES[gm].get("has_monthly"):
        await interaction.response.send_message(embed=discord.Embed(
            description=f"**{GAMEMODES[gm]['name']}** não tem stats mensais.",
            color=C_ERROR), ephemeral=True)
        return
    await send_stats_card(interaction, player, gm, "monthly", year=year, month=month)

# ── /season ───────────────────────────────────────────────────────────────────
@tree.command(name="season", description="Leaderboard de season do BedWars")
@app_commands.describe(season="Número da season (padrão: 1)", amount="Nº de jogadores (máx 100)")
async def cmd_season(interaction: discord.Interaction, season: int = 1, amount: int = 20):
    amount = max(1, min(amount, 100))
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        lb = await fetch_season_lb(s, season=season, amount=amount)
    if not lb or not isinstance(lb, list):
        await interaction.followup.send(embed=discord.Embed(
            description=f"Season {season} não encontrada.", color=C_ERROR))
        return
    lines = []
    for i, entry in enumerate(lb[:20]):
        name   = entry.get("human_index", entry.get("username", "?"))
        wins   = sg(entry, "victories")
        streak = sg(entry, "win_streak")
        lines.append(f"`#{i+1:02}`  `{name:<20}`  {wins:,} wins · {streak} streak")
    embed = discord.Embed(
        title=f"BedWars  ·  Season {season}  ·  Top {amount}",
        description="\n".join(lines), color=C_LB, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /seasonrank ───────────────────────────────────────────────────────────────
@tree.command(name="seasonrank", description="Ver rank de um jogador numa season do BedWars")
@app_commands.describe(player="Gamertag Xbox", season="Número da season (padrão: 1)")
async def cmd_season_rank(interaction: discord.Interaction,
                          player: str = None, season: int = 1):
    player = resolve_player(interaction, player)
    if not player:
        await interaction.response.send_message(embed=discord.Embed(
            description="Fornece um username ou liga com `/link`.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_season_player(s, player, season=season)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description=f"**{player}** não encontrado na season {season}.", color=C_ERROR))
        return
    lines = [f"`{FIELD_LABELS.get(k, k.replace('_',' ').title()):<20}` {fmt(v)}"
             for k, v in data.items() if isinstance(v, (int, float))]
    embed = discord.Embed(
        title=f"BedWars Season {season}  ·  {player}",
        description="\n".join(lines) or "Sem dados.", color=C_LB,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /search ───────────────────────────────────────────────────────────────────
@tree.command(name="search", description="Pesquisar jogadores por prefixo (mín. 4 letras)")
@app_commands.describe(prefix="Início do nome do jogador (4-15 caracteres)")
async def cmd_search(interaction: discord.Interaction, prefix: str):
    if len(prefix) < 4:
        await interaction.response.send_message(embed=discord.Embed(
            description="O prefixo precisa de ter pelo menos 4 caracteres.", color=C_ERROR),
            ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        results = await fetch_player_search(s, prefix)
    if not results or not isinstance(results, list):
        await interaction.followup.send(embed=discord.Embed(
            description=f"Nenhum jogador encontrado com o prefixo **{prefix}**.",
            color=C_ERROR))
        return
    lines = [f"`{r.get('username_cc', r.get('username', '?'))}`" for r in results[:10]]
    embed = discord.Embed(
        title=f"🔍 Resultados para \"{prefix}\"",
        description="\n".join(lines), color=C_INFO, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {len(results)} resultado(s)  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /globalstats ──────────────────────────────────────────────────────────────
@tree.command(name="globalstats", description="Estatísticas globais do servidor Hive")
async def cmd_global(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_global_stats(s)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description="Não foi possível obter estatísticas globais.", color=C_ERROR))
        return
    unique = dict(data.get("unique_players", {}))
    total  = unique.pop("global", 0)
    lines  = [f"**Total de jogadores únicos:** {total:,}\n"]
    for api_key, count in sorted(unique.items(), key=lambda x: -x[1]):
        name = next((v["name"] for v in GAMEMODES.values() if v["api"] == api_key), api_key)
        lines.append(f"`{name:<22}` {count:,}")
    embed = discord.Embed(
        title="🌍 Estatísticas Globais do Hive",
        description="\n".join(lines), color=C_CYAN, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /maps ─────────────────────────────────────────────────────────────────────
@tree.command(name="maps", description="Ver mapas disponíveis num modo de jogo")
@app_commands.describe(gamemode="Modo de jogo")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_maps(interaction: discord.Interaction,
                   gamemode: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bed"
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_game_maps(s, gm)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Sem mapas disponíveis para **{GAMEMODES[gm]['name']}**.",
            color=C_ERROR))
        return
    lines = [f"• {m.get('name', str(m))}" for m in data] if isinstance(data, list) else [str(data)]
    embed = discord.Embed(
        title=f"🗺️  {GAMEMODES[gm]['name']}  ·  Mapas",
        description="\n".join(lines) or "Sem mapas listados.",
        color=C_INFO, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /meta ─────────────────────────────────────────────────────────────────────
@tree.command(name="meta", description="Metadados de um modo (level unlocks, etc.)")
@app_commands.describe(gamemode="Modo de jogo")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def cmd_meta(interaction: discord.Interaction,
                   gamemode: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bed"
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        data = await fetch_game_meta(s, gm)
    if not data:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Sem metadados para **{GAMEMODES[gm]['name']}**.",
            color=C_ERROR))
        return
    preview = json.dumps(data, indent=2)[:3000]
    embed   = discord.Embed(
        title=f"⚙️  {GAMEMODES[gm]['name']}  ·  Meta",
        description=f"```json\n{preview}\n```",
        color=C_INFO, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.followup.send(embed=embed)

# ── /link ─────────────────────────────────────────────────────────────────────
@tree.command(name="link", description="Ligar o teu Discord à tua gamertag Xbox")
@app_commands.describe(player="A tua gamertag Xbox")
async def cmd_link(interaction: discord.Interaction, player: str):
    async with aiohttp.ClientSession() as s:
        profile = await fetch_profile(s, player)
    if not profile:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Não encontrei **{player}** no Hive. Verifica a tua gamertag.",
            color=C_ERROR), ephemeral=True)
        return
    db_link_user(interaction.user.id, player)
    await interaction.response.send_message(embed=discord.Embed(
        description=f"✅ **{player}** ligado ao teu Discord.",
        color=C_WIN), ephemeral=True)

# ── /unlink ───────────────────────────────────────────────────────────────────
@tree.command(name="unlink", description="Desligar a tua gamertag Xbox")
async def cmd_unlink(interaction: discord.Interaction):
    linked = db_get_linked(interaction.user.id)
    if not linked:
        await interaction.response.send_message(embed=discord.Embed(
            description="Não tens conta ligada.", color=C_ERROR), ephemeral=True)
        return
    db_unlink_user(interaction.user.id)
    await interaction.response.send_message(embed=discord.Embed(
        description=f"Conta **{linked}** desligada.", color=C_INFO), ephemeral=True)

# ── /track ────────────────────────────────────────────────────────────────────
@tree.command(name="track", description="Adicionar jogador ao tracking automático")
@app_commands.describe(player="Gamertag Xbox")
async def cmd_track(interaction: discord.Interaction, player: str):
    if len(TRACKED_PLAYERS) >= MAX_PLAYERS:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Limite atingido ({MAX_PLAYERS} jogadores). Usa `/untrack` primeiro.",
            color=C_ERROR))
        return
    if player.lower() in [p.lower() for p in TRACKED_PLAYERS]:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"**{player}** já está a ser tracked.", color=C_INFO))
        return
    TRACKED_PLAYERS.append(player)
    db_add_player(player)
    await interaction.response.send_message(embed=discord.Embed(
        description=f"✅ A fazer tracking de **{player}**  `{len(TRACKED_PLAYERS)}/{MAX_PLAYERS}`",
        color=C_WIN))

# ── /untrack ──────────────────────────────────────────────────────────────────
@tree.command(name="untrack", description="Remover jogador do tracking")
@app_commands.describe(player="Gamertag Xbox")
async def cmd_untrack(interaction: discord.Interaction, player: str):
    for p in TRACKED_PLAYERS:
        if p.lower() == player.lower():
            TRACKED_PLAYERS.remove(p)
            db_remove_player(p)
            await interaction.response.send_message(embed=discord.Embed(
                description=f"Parei de fazer tracking de **{player}**.", color=C_INFO))
            return
    await interaction.response.send_message(embed=discord.Embed(
        description=f"**{player}** não está na lista de tracking.", color=C_ERROR))

# ── /tracked ──────────────────────────────────────────────────────────────────
@tree.command(name="tracked", description="Ver jogadores em tracking")
async def cmd_tracked(interaction: discord.Interaction):
    if not TRACKED_PLAYERS:
        await interaction.response.send_message(embed=discord.Embed(
            description="Nenhum jogador em tracking.", color=C_INFO))
        return
    lines = [f"`{i+1}.` {p}" for i, p in enumerate(TRACKED_PLAYERS)]
    embed = discord.Embed(
        title=f"Jogadores em Tracking  ·  {len(TRACKED_PLAYERS)}/{MAX_PLAYERS}",
        description="\n".join(lines), color=C_INFO
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.response.send_message(embed=embed)

# ── /friendlb ─────────────────────────────────────────────────────────────────
@tree.command(name="friendlb", description="Leaderboard dos jogadores em tracking")
@app_commands.describe(gamemode="Modo de jogo", timeframe="Período")
@app_commands.choices(gamemode=GAMEMODE_CHOICES, timeframe=TIMEFRAME_CHOICES)
async def cmd_friendlb(interaction: discord.Interaction,
                       gamemode:  app_commands.Choice[str] = None,
                       timeframe: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bed"
    tf = timeframe.value if timeframe else "alltime"

    entries = []
    for p in TRACKED_PLAYERS:
        current = get_stored(p, gm)
        if not current:
            continue
        if tf in ("weekly", "daily"):
            snap = get_snapshot(p, gm, tf)
            if snap:
                # calcular delta
                d = {k: sg(current, k) - sg(snap, k) for k in GAMEMODES[gm]["fields"]}
            else:
                d = current
        else:
            d = current
        entries.append((p, sg(d, "victories"), sg(d, "kills"), sg(d, "win_streak"), win_rate(d)))

    if not entries:
        await interaction.response.send_message(embed=discord.Embed(
            description="Sem dados ainda. Aguarda o primeiro ciclo de verificação.",
            color=C_INFO))
        return
    entries.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    period_label = {"alltime": "All-Time", "monthly": "Monthly", "weekly": "Weekly", "daily": "Daily"}.get(tf)
    for i, (p, w, k, s, wr) in enumerate(entries):
        medal = medals[i] if i < 3 else f"`#{i+1:02}`"
        lines.append(f"{medal}  `{p:<20}`  **{w:,}** wins · {k:,} kills · {s} streak · {wr}% WR")
    embed = discord.Embed(
        title=f"{GAMEMODES[gm]['name']}  ·  Friend LB  [{period_label}]",
        description="\n".join(lines), color=C_LB, timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.response.send_message(embed=embed)

# ── /snapshot ─────────────────────────────────────────────────────────────────
@tree.command(name="snapshot", description="Forçar snapshot manual agora (para daily/weekly)")
@app_commands.describe(period="Tipo de snapshot")
@app_commands.choices(period=[
    app_commands.Choice(name="Daily",  value="daily"),
    app_commands.Choice(name="Weekly", value="weekly"),
])
async def cmd_snapshot(interaction: discord.Interaction,
                       period: app_commands.Choice[str] = None):
    p = period.value if period else "daily"
    await interaction.response.defer(ephemeral=True)
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    count = 0
    for player in list(TRACKED_PLAYERS):
        for gamemode in GAMEMODES:
            data = await fetch_stats(_session, player, gamemode)
            if data:
                save_snapshot(player, gamemode, p, data)
                count += 1
            await asyncio.sleep(0.5)
    await interaction.followup.send(embed=discord.Embed(
        description=f"✅ Snapshot **{p}** tirado para {count} combinações jogador/modo.",
        color=C_WIN), ephemeral=True)

# ── /gamemodes ────────────────────────────────────────────────────────────────
@tree.command(name="gamemodes", description="Listar todos os modos disponíveis")
async def cmd_gms(interaction: discord.Interaction):
    lines = []
    for k, v in GAMEMODES.items():
        extras = []
        if v.get("has_monthly"): extras.append("mensal")
        if v.get("has_seasons"): extras.append("seasons")
        extra = f"  _{', '.join(extras)}_" if extras else ""
        lines.append(f"`{k:<14}` **{v['name']}**{extra}")
    embed = discord.Embed(
        title="Modos de Jogo Disponíveis",
        description="\n".join(lines), color=C_INFO
    )
    embed.set_footer(text=f"Sniper 3.0  ·  {ts()}")
    await interaction.response.send_message(embed=embed)

# ── /help ─────────────────────────────────────────────────────────────────────
@tree.command(name="help", description="Ver todos os comandos")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Sniper 3.0 — Comandos",
        color=C_INFO, timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="📊 Stats por Gamemode", value=(
        "`/bedwars` `/treasurewars` `/skywars` `/skywars_classic` `/skywars_kits`\n"
        "`/murdermystery` `/groundwars` `/justbuild` `/capturetheflag` `/deathrun`\n"
        "`/hideandseek` `/blockdrop` `/bridge` `/partygames` `/survivalgames` `/gravity`\n"
        "_Todos suportam `[player]` e `[timeframe]`: all-time, monthly, weekly, daily_"
    ), inline=False)
    embed.add_field(name="📊 Stats Gerais", value=(
        "`/stats [player] [gamemode] [timeframe]` — Card visual de stats\n"
        "`/allstats [player]` — Resumo de todos os jogos\n"
        "`/monthlystats [player] [gm] [year] [month]` — Stats mensais\n"
        "`/profile [player]` — Perfil geral no Hive\n"
        "`/costume [player]` — Cosmético equipado\n"
        "`/compare <p1> <p2> [gm] [timeframe]` — Comparação lado a lado\n"
        "`/position [player] [gm]` — Rank global estimado\n"
        "`/parkour [player]` — Stats de Parkour Worlds\n"
        "`/seasonrank [player] [season]` — Rank na season BedWars"
    ), inline=False)
    embed.add_field(name="🏆 Leaderboards", value=(
        "`/lb [gm] [amount] [page]` — Leaderboard global\n"
        "`/monthlylb [gm] [year] [month]` — Leaderboard mensal\n"
        "`/monthlyavailable [gm]` — Meses disponíveis\n"
        "`/season [season] [amount]` — LB de season BedWars\n"
        "`/friendlb [gm] [timeframe]` — LB dos jogadores em tracking"
    ), inline=False)
    embed.add_field(name="🔍 Pesquisa & Info", value=(
        "`/search <prefix>` — Pesquisar jogadores\n"
        "`/globalstats` — Estatísticas globais\n"
        "`/maps [gm]` — Mapas de um modo\n"
        "`/meta [gm]` — Metadados de um modo\n"
        "`/gamemodes` — Listar todos os modos"
    ), inline=False)
    embed.add_field(name="🔗 Conta & Tracking", value=(
        "`/link <gamertag>` — Ligar conta Xbox\n"
        "`/unlink` — Desligar conta\n"
        "`/track <player>` — Adicionar ao tracking\n"
        "`/untrack <player>` — Remover do tracking\n"
        "`/tracked` — Ver jogadores em tracking\n"
        "`/snapshot [daily|weekly]` — Forçar snapshot manual"
    ), inline=False)
    embed.set_footer(text="Sniper 3.0  ·  Hive Bedrock Stats")
    await interaction.response.send_message(embed=embed)

# ── Ready ─────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    print(f"[BOT] {bot.user}  online  —  comandos sincronizados")
    print(f"[BOT] Tracking {len(TRACKED_PLAYERS)}/{MAX_PLAYERS}  ·  intervalo {CHECK_INTERVAL}s")
    if not track_loop.is_running():
        track_loop.start()
    if not leaderboard_loop.is_running():
        leaderboard_loop.start()
    if not snapshot_loop.is_running():
        snapshot_loop.start()

bot.run(BOT_TOKEN)
