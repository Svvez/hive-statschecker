import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import sqlite3
import json
import os
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN       = os.environ["BOT_TOKEN"]
CHANNEL_ID      = int(os.environ["CHANNEL_ID"])
TRACKED_PLAYERS = os.environ.get("TRACKED_PLAYERS", "donofigo445").split(",")
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "30"))

# ── Intents ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Gamemodes ─────────────────────────────────────────────────────────────────
GAMEMODES = {
    "bedwars":  {"name": "Bed Wars",         "color": 0xE74C3C, "fields": ["played","victories","kills","deaths","finals","beds_destroyed","win_streak"]},
    "skywars":  {"name": "Sky Wars",         "color": 0x3498DB, "fields": ["played","victories","kills","deaths","win_streak"]},
    "murder":   {"name": "Murder Mystery",   "color": 0x9B59B6, "fields": ["played","victories","kills","deaths","win_streak"]},
    "ground":   {"name": "Ground Wars",      "color": 0xE67E22, "fields": ["played","victories","kills","deaths","win_streak"]},
    "build":    {"name": "Just Build",       "color": 0x1ABC9C, "fields": ["played","victories"]},
    "ctf":      {"name": "Capture The Flag", "color": 0xE74C3C, "fields": ["played","victories","kills","deaths","flags_captured","win_streak"]},
    "dr":       {"name": "Deathrun",         "color": 0x2C3E50, "fields": ["played","victories","deaths","win_streak"]},
    "hide":     {"name": "Hide & Seek",      "color": 0x27AE60, "fields": ["played","victories","win_streak"]},
    "drop":     {"name": "Block Drop",       "color": 0x795548, "fields": ["played","victories","win_streak"]},
    "sky":      {"name": "Sky Royale",       "color": 0x00BCD4, "fields": ["played","victories","kills","deaths","win_streak"]},
    "bridge":   {"name": "The Bridge",       "color": 0x607D8B, "fields": ["played","victories","kills","deaths","goals","win_streak"]},
    "party":    {"name": "Party Games",      "color": 0xF1C40F, "fields": ["played","victories","win_streak"]},
}

FIELD_LABELS = {
    "played":         "Games Played",
    "victories":      "Wins",
    "kills":          "Kills",
    "deaths":         "Deaths",
    "finals":         "Final Kills",
    "beds_destroyed": "Beds Broken",
    "win_streak":     "Win Streak",
    "flags_captured": "Flags Captured",
    "goals":          "Goals",
}

GAMEMODE_CHOICES = [
    app_commands.Choice(name=v["name"], value=k)
    for k, v in GAMEMODES.items()
]

API_BASE = "https://api.playhive.com/v0"

# ── Database ──────────────────────────────────────────────────────────────────
conn = sqlite3.connect("hive_tracker.db")
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS stats (
        player   TEXT,
        gamemode TEXT,
        data     TEXT,
        PRIMARY KEY (player, gamemode)
    )
""")
conn.commit()

def get_stored(player, gamemode):
    cursor.execute("SELECT data FROM stats WHERE player=? AND gamemode=?",
                   (player.lower(), gamemode))
    row = cursor.fetchone()
    return json.loads(row[0]) if row else None

def save_stats(player, gamemode, data):
    cursor.execute("REPLACE INTO stats (player, gamemode, data) VALUES (?,?,?)",
                   (player.lower(), gamemode, json.dumps(data)))
    conn.commit()

# ── API ───────────────────────────────────────────────────────────────────────
async def fetch_stats(session, player, gamemode):
    url = f"{API_BASE}/game/all/{gamemode}/{player}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            print(f"[DEBUG] {player} {gamemode} → HTTP {r.status}")
            if r.status == 200:
                data = await r.json()
                print(f"[DEBUG] {player} {gamemode} keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                return data
            return None
    except Exception as e:
        print(f"[ERROR] fetch_stats {player} {gamemode}: {e}")
        return None

async def fetch_monthly(session, player, gamemode):
    url = f"{API_BASE}/game/monthly/player/{gamemode}/{player}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                return data[0] if isinstance(data, list) and data else None
            return None
    except Exception as e:
        print(f"[ERROR] fetch_monthly {player} {gamemode}: {e}")
        return None

# ── Helpers ───────────────────────────────────────────────────────────────────
def safe_get(data, key):
    if not data:
        return 0
    if key == "win_streak":
        return data.get("win_streak", data.get("winning_streak", 0))
    return data.get(key, 0)

def calc_losses(data):
    return max(0, safe_get(data, "played") - safe_get(data, "victories"))

def fmt_diff(diff):
    if diff > 0: return f"  **(+{diff})**"
    if diff < 0: return f"  **({diff})**"
    return ""

def ratio(a, b):
    if b == 0:
        return str(a) if a == 0 else f"{a}/0"
    return str(round(a / b, 2))

# ── Embed ─────────────────────────────────────────────────────────────────────
def build_embed(player, gamemode, new_data, old_data, monthly):
    gm = GAMEMODES[gamemode]

    wins_diff   = safe_get(new_data, "victories") - safe_get(old_data, "victories")
    losses_new  = calc_losses(new_data)
    losses_diff = losses_new - calc_losses(old_data)

    color = gm["color"] if wins_diff > 0 else 0x2F3136

    embed = discord.Embed(
        title=f"{player}  ·  {gm['name']}",
        color=color,
        timestamp=datetime.utcnow()
    )

    if wins_diff > 0:
        embed.description = f"**+{wins_diff} win{'s' if wins_diff != 1 else ''}** this session"
    elif losses_diff > 0:
        embed.description = f"**+{losses_diff} loss{'es' if losses_diff != 1 else ''}** this session"
    else:
        embed.description = "Stats updated"

    # ── All-Time ──
    lines = []
    for field in gm["fields"]:
        label = FIELD_LABELS.get(field, field.replace("_", " ").title())
        val_new = safe_get(new_data, field)
        val_old = safe_get(old_data, field)
        lines.append(f"`{label:<16}` {val_new}{fmt_diff(val_new - val_old)}")

    # Losses
    lines.insert(2, f"`{'Losses':<16}` {losses_new}{fmt_diff(losses_diff)}")

    # Ratios
    k  = safe_get(new_data, "kills")
    d  = safe_get(new_data, "deaths")
    w  = safe_get(new_data, "victories")
    l  = losses_new
    has_kd = "kills" in gm["fields"] and "deaths" in gm["fields"]
    if has_kd:
        lines.append(f"`{'K/D':<16}` {ratio(k, d)}")
    lines.append(f"`{'W/L':<16}` {ratio(w, l)}")

    embed.add_field(name="ALL-TIME", value="\n".join(lines), inline=False)

    # ── This Month ──
    if monthly:
        m_lines = []
        for field in gm["fields"]:
            label = FIELD_LABELS.get(field, field.replace("_", " ").title())
            m_lines.append(f"`{label:<16}` {safe_get(monthly, field)}")
        m_losses = calc_losses(monthly)
        m_lines.insert(2, f"`{'Losses':<16}` {m_losses}")
        mk = safe_get(monthly, "kills")
        md = safe_get(monthly, "deaths")
        mw = safe_get(monthly, "victories")
        if has_kd:
            m_lines.append(f"`{'K/D':<16}` {ratio(mk, md)}")
        m_lines.append(f"`{'W/L':<16}` {ratio(mw, m_losses)}")
        embed.add_field(name="THIS MONTH", value="\n".join(m_lines), inline=False)

    embed.set_footer(text=f"Hive Tracker  ·  {datetime.utcnow().strftime('%d %b %Y  %H:%M')} UTC")
    return embed

# ── Tracking loop ─────────────────────────────────────────────────────────────
_session = None

@tasks.loop(seconds=CHECK_INTERVAL)
async def track_loop():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[ERROR] Channel {CHANNEL_ID} not found.")
        return

    for player in list(TRACKED_PLAYERS):
        for gamemode in GAMEMODES:
            new_data = await fetch_stats(_session, player, gamemode)
            if not new_data:
                await asyncio.sleep(0.5)
                continue

            old_data = get_stored(player, gamemode)

            if old_data is None:
                save_stats(player, gamemode, new_data)
                print(f"[INFO] First snapshot: {player} / {gamemode}")
                await asyncio.sleep(0.5)
                continue

            changed = [
                f for f in GAMEMODES[gamemode]["fields"]
                if safe_get(new_data, f) != safe_get(old_data, f)
            ]
            if calc_losses(new_data) != calc_losses(old_data):
                changed.append("losses")

            if changed:
                monthly = await fetch_monthly(_session, player, gamemode)
                save_stats(player, gamemode, new_data)
                embed = build_embed(player, gamemode, new_data, old_data, monthly)
                try:
                    await channel.send(embed=embed)
                    print(f"[INFO] Sent: {player} / {gamemode} → {changed}")
                except Exception as e:
                    print(f"[ERROR] Send failed: {e}")

            await asyncio.sleep(0.5)

# ── Slash Commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="stats", description="Ver stats de um jogador")
@app_commands.describe(player="Nome do jogador", gamemode="Gamemode (padrão: bedwars)")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def slash_stats(interaction: discord.Interaction, player: str, gamemode: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bedwars"
    await interaction.response.defer()
    async with aiohttp.ClientSession() as s:
        new_data = await fetch_stats(s, player, gm)
        monthly  = await fetch_monthly(s, player, gm)
    if not new_data:
        await interaction.followup.send(f"No data found for **{player}**.")
        return
    old_data = get_stored(player, gm) or new_data
    await interaction.followup.send(embed=build_embed(player, gm, new_data, old_data, monthly))

@bot.tree.command(name="track", description="Adicionar jogador ao tracking")
@app_commands.describe(player="Nome do jogador")
async def slash_track(interaction: discord.Interaction, player: str):
    if player.lower() in [p.lower() for p in TRACKED_PLAYERS]:
        await interaction.response.send_message(f"**{player}** is already being tracked.", ephemeral=True)
        return
    TRACKED_PLAYERS.append(player)
    await interaction.response.send_message(f"Now tracking **{player}** across all gamemodes.")

@bot.tree.command(name="untrack", description="Remover jogador do tracking")
@app_commands.describe(player="Nome do jogador")
async def slash_untrack(interaction: discord.Interaction, player: str):
    global TRACKED_PLAYERS
    match = next((p for p in TRACKED_PLAYERS if p.lower() == player.lower()), None)
    if match:
        TRACKED_PLAYERS = [p for p in TRACKED_PLAYERS if p.lower() != player.lower()]
        await interaction.response.send_message(f"Stopped tracking **{match}**.")
    else:
        await interaction.response.send_message(f"**{player}** is not being tracked.", ephemeral=True)

@bot.tree.command(name="tracked", description="Ver todos os jogadores rastreados")
async def slash_tracked(interaction: discord.Interaction):
    if not TRACKED_PLAYERS:
        await interaction.response.send_message("No players are currently being tracked.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Tracked Players",
        description="\n".join(f"`{p}`" for p in TRACKED_PLAYERS),
        color=0x2F3136
    )
    embed.set_footer(text=f"{len(TRACKED_PLAYERS)} player(s) tracked")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Ver leaderboard dos jogadores rastreados")
@app_commands.describe(gamemode="Gamemode (padrão: bedwars)")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def slash_lb(interaction: discord.Interaction, gamemode: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "bedwars"
    entries = [
        (p, safe_get(get_stored(p, gm), "victories"), safe_get(get_stored(p, gm), "kills"))
        for p in TRACKED_PLAYERS if get_stored(p, gm)
    ]
    if not entries:
        await interaction.response.send_message("No data yet.", ephemeral=True)
        return
    entries.sort(key=lambda x: x[1], reverse=True)
    gm_info = GAMEMODES[gm]
    lines = [
        f"`#{i+1}` **{p}** — {w} wins · {k} kills"
        for i, (p, w, k) in enumerate(entries)
    ]
    embed = discord.Embed(
        title=f"{gm_info['name']} — Leaderboard",
        description="\n".join(lines),
        color=gm_info["color"]
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="gamemodes", description="Ver todos os gamemodes disponíveis")
async def slash_gamemodes(interaction: discord.Interaction):
    lines = [f"`{k:<10}` {v['name']}" for k, v in GAMEMODES.items()]
    embed = discord.Embed(
        title="Available Gamemodes",
        description="\n".join(lines),
        color=0x2F3136
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="Ver todos os comandos e regras do bot")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Hive Tracker — Help",
        color=0x2F3136
    )
    embed.add_field(
        name="COMMANDS",
        value=(
            "`/stats <player> [gamemode]` — View a player's stats\n"
            "`/track <player>` — Add a player to tracking\n"
            "`/untrack <player>` — Remove a player from tracking\n"
            "`/tracked` — List all tracked players\n"
            "`/leaderboard [gamemode]` — Leaderboard of tracked players\n"
            "`/gamemodes` — List available gamemodes\n"
            "`/debug <player> [gamemode]` — View raw API data\n"
            "`/help` — Show this message"
        ),
        inline=False
    )
    embed.add_field(
        name="HOW IT WORKS",
        value=(
            f"The bot checks stats every **{CHECK_INTERVAL}s**.\n"
            "When a stat changes, an update is posted in the tracker channel.\n"
            "Losses are calculated as `played − wins` since the API does not return them directly.\n"
            "The first time a player is tracked, a snapshot is saved — no notification is sent."
        ),
        inline=False
    )
    embed.add_field(
        name="RULES",
        value=(
            "— Do not track players without their knowledge.\n"
            "— Do not spam `/track` to overload the API.\n"
            "— Keep the tracker channel clean — do not post unrelated messages there.\n"
            f"— Minimum check interval is 30s to avoid API rate limits."
        ),
        inline=False
    )
    embed.set_footer(text="Hive Tracker  ·  Powered by api.playhive.com")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="debug", description="Ver JSON raw da API para um jogador")
@app_commands.describe(player="Nome do jogador", gamemode="Gamemode")
@app_commands.choices(gamemode=GAMEMODE_CHOICES)
async def slash_debug(interaction: discord.Interaction, player: str, gamemode: app_commands.Choice[str] = None):
    gm = gamemode.value if gamemode else "murder"
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as s:
        data = await fetch_stats(s, player, gm)
    if not data:
        await interaction.followup.send("No data returned from API.", ephemeral=True)
        return
    text = json.dumps(data, indent=2)
    await interaction.followup.send(f"```json\n{text[:1900]}\n```", ephemeral=True)

# ── Prefix fallback ───────────────────────────────────────────────────────────

@bot.command(name="stats")
async def cmd_stats(ctx, player: str, gamemode: str = "bedwars"):
    gamemode = gamemode.lower()
    if gamemode not in GAMEMODES:
        await ctx.send(f"Invalid gamemode. Options: {', '.join(GAMEMODES.keys())}")
        return
    async with aiohttp.ClientSession() as s:
        new_data = await fetch_stats(s, player, gamemode)
        monthly  = await fetch_monthly(s, player, gamemode)
    if not new_data:
        await ctx.send(f"No data found for **{player}**.")
        return
    old_data = get_stored(player, gamemode) or new_data
    await ctx.send(embed=build_embed(player, gamemode, new_data, old_data, monthly))

@bot.command(name="track")
async def cmd_track(ctx, player: str):
    if player.lower() in [p.lower() for p in TRACKED_PLAYERS]:
        await ctx.send(f"**{player}** is already being tracked.")
        return
    TRACKED_PLAYERS.append(player)
    await ctx.send(f"Now tracking **{player}**.")

@bot.command(name="untrack")
async def cmd_untrack(ctx, player: str):
    global TRACKED_PLAYERS
    match = next((p for p in TRACKED_PLAYERS if p.lower() == player.lower()), None)
    if match:
        TRACKED_PLAYERS = [p for p in TRACKED_PLAYERS if p.lower() != player.lower()]
        await ctx.send(f"Stopped tracking **{match}**.")
    else:
        await ctx.send(f"**{player}** is not being tracked.")

@bot.command(name="tracked")
async def cmd_tracked(ctx):
    if not TRACKED_PLAYERS:
        await ctx.send("No players are currently being tracked.")
        return
    embed = discord.Embed(
        title="Tracked Players",
        description="\n".join(f"`{p}`" for p in TRACKED_PLAYERS),
        color=0x2F3136
    )
    await ctx.send(embed=embed)

@bot.command(name="leaderboard", aliases=["lb"])
async def cmd_lb(ctx, gamemode: str = "bedwars"):
    gamemode = gamemode.lower()
    if gamemode not in GAMEMODES:
        await ctx.send("Invalid gamemode.")
        return
    entries = [
        (p, safe_get(get_stored(p, gamemode), "victories"), safe_get(get_stored(p, gamemode), "kills"))
        for p in TRACKED_PLAYERS if get_stored(p, gamemode)
    ]
    if not entries:
        await ctx.send("No data yet.")
        return
    entries.sort(key=lambda x: x[1], reverse=True)
    gm = GAMEMODES[gamemode]
    lines = [f"`#{i+1}` **{p}** — {w} wins · {k} kills" for i, (p, w, k) in enumerate(entries)]
    embed = discord.Embed(
        title=f"{gm['name']} — Leaderboard",
        description="\n".join(lines),
        color=gm["color"]
    )
    await ctx.send(embed=embed)

@bot.command(name="gamemodes", aliases=["gms"])
async def cmd_gms(ctx):
    lines = [f"`{k:<10}` {v['name']}" for k, v in GAMEMODES.items()]
    embed = discord.Embed(title="Available Gamemodes", description="\n".join(lines), color=0x2F3136)
    await ctx.send(embed=embed)

# ── Ready ─────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"[BOT] Online as {bot.user}")
    print(f"[BOT] Tracking {len(TRACKED_PLAYERS)} player(s) every {CHECK_INTERVAL}s")
    try:
        synced = await bot.tree.sync()
        print(f"[BOT] {len(synced)} slash commands synced")
    except Exception as e:
        print(f"[ERROR] Sync failed: {e}")
    track_loop.start()

bot.run(BOT_TOKEN)
