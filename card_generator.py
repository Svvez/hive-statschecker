"""
card_generator.py  —  Sniper 3.0
Melhorias vs v2:
  - GAMEMODES dict sincronizado com bot.py (era a principal fonte de bugs)
  - Suporte a sky-classic, sky-kits, grav, party, wars corrigido
  - Timeframe label (DAILY / WEEKLY / MONTHLY / ALL-TIME) no card
  - Delta com seta ↑↓ mais legível
  - Profile card inclui equipped_hat
  - LB card corrigido para mostrar cores correctas por gamemode
"""

from PIL import Image, ImageDraw, ImageFont
import io
import os
import math
from datetime import datetime, timezone

# ── Palette ───────────────────────────────────────────────────────────────────
BG_DARK     = (13,  15,  20)
BG_MID      = (17,  21,  30)
BG_CARD     = (11,  14,  24)
BG_CELL     = (10,  12,  18)
ACCENT_CYAN = (0,   229, 255)
ACCENT_PURP = (124, 77,  255)
ACCENT_GRN  = (0,   230, 118)
ACCENT_AMB  = (255, 171, 64)
ACCENT_RED  = (255, 82,  82)
WHITE       = (255, 255, 255)
GRAY_LT     = (160, 174, 192)
GRAY_DK     = (74,  85,  104)
GOLD        = (255, 215, 0)

# ── Gamemode colors — mapeados pela chave real da API (igual ao bot.py) ───────
GAMEMODE_COLORS = {
    "bed":        (255, 82,  82),
    "wars":       (255, 171, 64),
    "sky":        (0,   229, 255),
    "sky-classic":(0,   200, 220),
    "sky-kits":   (0,   180, 200),
    "murder":     (255, 171, 64),
    "ground":     (0,   230, 118),
    "build":      (124, 77,  255),
    "ctf":        (0,   229, 255),
    "dr":         (255, 82,  82),
    "hide":       (0,   230, 118),
    "drop":       (255, 171, 64),
    "bridge":     (0,   229, 255),
    "party":      (255, 105, 180),
    "sg":         (255, 82,  82),
    "grav":       (124, 77,  255),
}

# ── Gamemodes — SINCRONIZADO com bot.py ───────────────────────────────────────
GAMEMODES = {
    "bed":        {"name": "BedWars",           "fields": ["played","victories","losses","kills","deaths","finals","beds_destroyed","win_streak"]},
    "wars":       {"name": "Treasure Wars",     "fields": ["played","victories","losses","kills","deaths","finals","treasures_destroyed","win_streak"]},
    "sky":        {"name": "Sky Wars",          "fields": ["played","victories","losses","kills","deaths","win_streak"]},
    "sky-classic":{"name": "Sky Wars Classic",  "fields": ["played","victories","losses","kills","deaths","win_streak"]},
    "sky-kits":   {"name": "Sky Wars Kits",     "fields": ["played","victories","losses","kills","deaths","win_streak"]},
    "murder":     {"name": "Murder Mystery",    "fields": ["played","victories","losses","kills","deaths","win_streak"]},
    "ground":     {"name": "Ground Wars",       "fields": ["played","victories","losses","kills","deaths","win_streak"]},
    "build":      {"name": "Just Build",        "fields": ["played","victories","losses"]},
    "ctf":        {"name": "Capture the Flag",  "fields": ["played","victories","losses","kills","deaths","flags_captured","win_streak"]},
    "dr":         {"name": "Deathrun",          "fields": ["played","victories","losses","deaths","win_streak"]},
    "hide":       {"name": "Hide & Seek",       "fields": ["played","victories","losses","deaths","hider_kills","seeker_kills","win_streak"]},
    "drop":       {"name": "Block Drop",        "fields": ["played","victories","losses","win_streak"]},
    "bridge":     {"name": "The Bridge",        "fields": ["played","victories","losses","kills","deaths","goals","win_streak"]},
    "party":      {"name": "Party Games",       "fields": ["played","victories","losses","win_streak"]},
    "sg":         {"name": "Survival Games",    "fields": ["played","victories","losses","kills","deaths","teleporters_used","launchpads_used","flares_used","win_streak"]},
    "grav":       {"name": "Gravity",           "fields": ["played","victories","losses","win_streak"]},
}

FIELD_LABELS = {
    "played":               "GAMES",
    "victories":            "WINS",
    "losses":               "LOSSES",
    "kills":                "KILLS",
    "deaths":               "DEATHS",
    "finals":               "FINALS",
    "beds_destroyed":       "BEDS",
    "treasures_destroyed":  "TREASURES",
    "win_streak":           "STREAK",
    "flags_captured":       "FLAGS",
    "hider_kills":          "HIDER K",
    "seeker_kills":         "SEEKER K",
    "goals":                "GOALS",
    "teleporters_used":     "TELEPORT",
    "launchpads_used":      "LAUNCHPAD",
    "flares_used":          "FLARES",
}

# ── Font loader ───────────────────────────────────────────────────────────────
def load_font(size, bold=False):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf" if bold else "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()

# ── Drawing helpers ───────────────────────────────────────────────────────────
def draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    if fill or outline:
        draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill,
                                outline=outline, width=width)

def draw_gradient_bar(img, x, y, w, h, color_left, color_right):
    draw = ImageDraw.Draw(img)
    for i in range(w):
        t = i / max(w - 1, 1)
        r = int(color_left[0] + t * (color_right[0] - color_left[0]))
        g = int(color_left[1] + t * (color_right[1] - color_left[1]))
        b = int(color_left[2] + t * (color_right[2] - color_left[2]))
        draw.line([(x + i, y), (x + i, y + h)], fill=(r, g, b))

def text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def draw_centered(draw, cx, y, text, font, color):
    w, _ = text_size(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=color)

def sg(data, key):
    return data.get(key, 0) if data else 0

def kdr(data):
    k, d = sg(data, "kills"), sg(data, "deaths")
    return round(k / max(d, 1), 2) if (k or d) else None

def win_rate(data):
    w, p = sg(data, "victories"), sg(data, "played")
    return round((w / max(p, 1)) * 100, 1) if p else 0.0

def diff_arrow(d):
    """Seta ↑↓ com o valor, mais legível que (±N)."""
    if isinstance(d, float):
        if d > 0:   return f"↑{d:.2f}"
        if d < 0:   return f"↓{abs(d):.2f}"
        return "—"
    if isinstance(d, int):
        if d > 0:   return f"↑{d}"
        if d < 0:   return f"↓{abs(d)}"
        return "—"
    return "—"

def format_num(n):
    if isinstance(n, float):
        return f"{n:.2f}"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def _footer_ts():
    return datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

# ── Stats Card ────────────────────────────────────────────────────────────────
def generate_stats_card(
    player: str,
    gamemode: str,
    new: dict,
    old: dict = None,
    monthly: dict = None,
    timeframe: str = "alltime"
) -> bytes:
    W, H = 600, 440
    gm_info  = GAMEMODES.get(gamemode, {"name": gamemode.title(), "fields": list((new or {}).keys())[:8]})
    gm_color = GAMEMODE_COLORS.get(gamemode, ACCENT_CYAN)
    fields   = gm_info["fields"]

    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    f_title  = load_font(26, bold=True)
    f_sub    = load_font(12)
    f_label  = load_font(10)
    f_value  = load_font(22, bold=True)
    f_diff   = load_font(10)
    f_small  = load_font(9)
    f_medium = load_font(13, bold=True)
    f_badge  = load_font(11, bold=True)

    # ── Header ────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, 70], fill=BG_MID)
    draw_gradient_bar(img, 0, 0, W, 3, ACCENT_PURP, gm_color)

    # Corner decorators
    for cx, cy, dx, dy in [(0,0,1,1),(W,0,-1,1),(0,H,1,-1),(W,H,-1,-1)]:
        for i in range(10):
            draw.point((cx+dx*i, cy), fill=gm_color)
            draw.point((cx, cy+dy*i), fill=gm_color)

    draw.text((18, 12), player.upper(), font=f_title, fill=WHITE)

    # Gamemode badge
    badge_text = gm_info["name"].upper()
    bw, bh = text_size(draw, badge_text, f_sub)
    bx = W - bw - 32
    draw_rounded_rect(draw, [bx-8, 16, bx+bw+8, 16+bh+6], radius=4,
                      fill=(gm_color[0]//6, gm_color[1]//6, gm_color[2]//6),
                      outline=gm_color, width=1)
    draw.text((bx, 19), badge_text, font=f_sub, fill=gm_color)

    # Timeframe badge
    tf_labels = {"alltime": "ALL-TIME", "monthly": "MONTHLY", "weekly": "WEEKLY", "daily": "DAILY"}
    tf_text = tf_labels.get(timeframe, timeframe.upper())
    tw, th = text_size(draw, tf_text, f_badge)
    tx = W - tw - 32
    draw_rounded_rect(draw, [tx-8, 38, tx+tw+8, 38+th+6], radius=4,
                      fill=(40, 44, 60), outline=GRAY_DK, width=1)
    draw.text((tx, 41), tf_text, font=f_badge, fill=GRAY_LT)

    # Win rate & KD
    wr  = win_rate(new)
    kd  = kdr(new)
    info_parts = [f"WIN RATE  {wr}%"]
    if kd is not None:
        info_parts.append(f"K/D  {kd}")
    draw.text((18, 48), "   ·   ".join(info_parts), font=f_small, fill=GRAY_DK)

    draw_gradient_bar(img, 0, 70, W, 1, (30, 40, 60), (30, 40, 60))

    # ── Stats grid ────────────────────────────────────────────────────────────
    cols      = 4
    all_stats = []

    for field in fields:
        label = FIELD_LABELS.get(field, field.upper()[:8])
        val   = sg(new, field)
        diff  = (sg(new, field) - sg(old, field)) if old else None
        color = WHITE
        if field == "victories":           color = ACCENT_GRN
        elif field in ("kills","finals"):  color = ACCENT_AMB
        elif field in ("deaths","losses"): color = ACCENT_RED
        elif field == "win_streak":        color = ACCENT_CYAN
        all_stats.append((label, val, diff, color))

    kd_val = kdr(new)
    if kd_val is not None:
        kd_old   = kdr(old) if old else None
        kd_diff  = round(kd_val - (kd_old or 0), 2) if kd_old is not None else None
        all_stats.append(("K/D", kd_val, kd_diff, ACCENT_CYAN))

    wr_old  = win_rate(old) if old else None
    wr_diff = round(wr - wr_old, 1) if wr_old is not None else None
    all_stats.append(("WIN RATE", f"{wr}%",
                      f"{'+' if (wr_diff or 0)>0 else ''}{wr_diff}%" if wr_diff is not None else None,
                      ACCENT_GRN))

    grid_top = 78
    cell_w   = W // cols
    cell_h   = 70

    for i, (label, val, diff, col) in enumerate(all_stats):
        col_i = i % cols
        row_i = i // cols
        cx    = col_i * cell_w
        cy    = grid_top + row_i * cell_h
        bg    = BG_CARD if (col_i + row_i) % 2 == 0 else BG_CELL
        draw.rectangle([cx, cy, cx+cell_w-1, cy+cell_h-1], fill=bg)
        draw_centered(draw, cx+cell_w//2, cy+7, str(label), f_label, GRAY_DK)
        val_str = format_num(val) if isinstance(val, int) else str(val)
        draw_centered(draw, cx+cell_w//2, cy+22, val_str, f_value, col)
        if diff is not None:
            diff_val = diff if isinstance(diff, str) else diff_arrow(diff)
            if diff_val and diff_val != "—":
                diff_col = ACCENT_GRN if str(diff_val).startswith("↑") or str(diff_val).startswith("+") else ACCENT_RED
            else:
                diff_col = GRAY_DK
            draw_centered(draw, cx+cell_w//2, cy+50, diff_val or "—", f_diff, diff_col)

    # ── Monthly section ────────────────────────────────────────────────────────
    grid_bottom = grid_top + math.ceil(len(all_stats) / cols) * cell_h

    if monthly and timeframe == "alltime":
        mo_top = grid_bottom + 4
        if mo_top + 80 <= H:
            draw.rectangle([0, mo_top, W, mo_top+18], fill=(15, 18, 28))
            draw.text((18, mo_top+4), "THIS MONTH", font=f_label, fill=ACCENT_PURP)
            mo_stats  = []
            mo_fields = [f for f in fields if f in FIELD_LABELS][:8]
            for field in mo_fields:
                label = FIELD_LABELS.get(field, field.upper()[:8])
                val   = sg(monthly, field)
                color = WHITE
                if field == "victories":           color = ACCENT_GRN
                elif field in ("kills","finals"):  color = ACCENT_AMB
                elif field in ("deaths","losses"): color = ACCENT_RED
                elif field == "win_streak":        color = ACCENT_CYAN
                mo_stats.append((label, val, color))
            mo_cell_h = 54
            for i, (label, val, col) in enumerate(mo_stats):
                col_i = i % cols
                row_i = i // cols
                cx    = col_i * cell_w
                cy    = mo_top + 18 + row_i * mo_cell_h
                bg    = (12, 14, 22) if (col_i+row_i) % 2 == 0 else (9, 11, 18)
                draw.rectangle([cx, cy, cx+cell_w-1, cy+mo_cell_h-1], fill=bg)
                draw_centered(draw, cx+cell_w//2, cy+5, str(label), f_label, GRAY_DK)
                draw_centered(draw, cx+cell_w//2, cy+20, format_num(val), f_medium, col)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_text = f"SNIPER 3.0  ·  {_footer_ts()}"
    draw.rectangle([0, H-20, W, H], fill=(8, 10, 15))
    draw_gradient_bar(img, 0, H-20, W, 1, ACCENT_PURP, gm_color)
    fw, _ = text_size(draw, footer_text, f_small)
    draw.text((W-fw-10, H-14), footer_text, font=f_small, fill=GRAY_DK)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Profile Card ──────────────────────────────────────────────────────────────
def generate_profile_card(player: str, profile: dict) -> bytes:
    W, H = 600, 320
    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    f_title  = load_font(28, bold=True)
    f_sub    = load_font(12)
    f_label  = load_font(10)
    f_value  = load_font(20, bold=True)
    f_small  = load_font(9)

    draw.rectangle([0, 0, W, 68], fill=BG_MID)
    draw_gradient_bar(img, 0, 0, W, 3, ACCENT_PURP, ACCENT_CYAN)

    draw.text((18, 14), player.upper(), font=f_title, fill=WHITE)
    xp = profile.get("xp", 0)
    draw.text((18, 48), f"XP: {xp:,}", font=f_sub, fill=GRAY_LT)

    # Hive+ badge
    if profile.get("paid_rank"):
        hw, hh = text_size(draw, "HIVE+", f_sub)
        hx = W - hw - 24
        draw_rounded_rect(draw, [hx-8, 20, hx+hw+8, 20+hh+6], radius=4,
                          fill=(255, 200, 0, 50), outline=GOLD, width=1)
        draw.text((hx, 23), "HIVE+", font=f_sub, fill=GOLD)

    # Stats grid — 5 cells
    hat      = profile.get("equipped_hat", None)
    costume  = profile.get("equipped_hub_title", "None")
    if isinstance(costume, dict):
        costume = costume.get("title", "None")

    stats = [
        ("ACCOUNT XP",  f"{xp:,}",                WHITE),
        ("FIRST SEEN",  str(profile.get("first_played_hive", "N/A"))[:10], ACCENT_CYAN),
        ("HIVE+",       "YES" if profile.get("paid_rank") else "NO",
                        ACCENT_GRN if profile.get("paid_rank") else GRAY_DK),
        ("HUB TITLE",   str(costume)[:16],         ACCENT_AMB),
        ("HAT",         str(hat)[:16] if hat else "None", ACCENT_PURP),
    ]

    cols   = 5
    cell_w = W // cols
    for i, (label, val, col) in enumerate(stats):
        cx = i * cell_w
        bg = BG_CARD if i % 2 == 0 else BG_CELL
        draw.rectangle([cx, 72, cx+cell_w-1, 200], fill=bg)
        draw_centered(draw, cx+cell_w//2, 82,  label, f_label, GRAY_DK)
        draw_centered(draw, cx+cell_w//2, 105, str(val), f_value, col)

    # Footer
    footer_text = f"SNIPER 3.0  ·  {_footer_ts()}"
    draw.rectangle([0, H-20, W, H], fill=(8, 10, 15))
    draw_gradient_bar(img, 0, H-20, W, 1, ACCENT_PURP, ACCENT_CYAN)
    fw, _ = text_size(draw, footer_text, f_small)
    draw.text((W-fw-10, H-14), footer_text, font=f_small, fill=GRAY_DK)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Leaderboard Card ──────────────────────────────────────────────────────────
def generate_lb_card(gamemode: str, lb: list) -> bytes:
    entries  = lb[:20]
    W        = 600
    H        = 80 + len(entries) * 36 + 30
    gm_color = GAMEMODE_COLORS.get(gamemode, ACCENT_CYAN)
    gm_name  = GAMEMODES.get(gamemode, {}).get("name", gamemode.title())

    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    f_title = load_font(20, bold=True)
    f_sub   = load_font(11)
    f_row   = load_font(12, bold=True)
    f_small = load_font(9)

    draw.rectangle([0, 0, W, 54], fill=BG_MID)
    draw_gradient_bar(img, 0, 0, W, 3, ACCENT_PURP, gm_color)
    draw.text((18, 12), f"{gm_name.upper()}  ·  TOP {len(entries)}", font=f_title, fill=WHITE)
    draw.text((18, 38), "WIN STREAK LEADERBOARD", font=f_sub, fill=GRAY_DK)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for i, entry in enumerate(entries):
        rank   = i + 1
        name   = entry.get("human_index", entry.get("username", "Unknown"))
        streak = sg(entry, "win_streak")
        wins   = sg(entry, "victories")
        played = sg(entry, "played")
        wr     = round((wins / max(played, 1)) * 100, 1) if played else 0

        y  = 58 + i * 36
        bg = BG_MID if i % 2 == 0 else BG_CARD
        draw.rectangle([0, y, W, y+35], fill=bg)

        rank_col = GOLD if rank == 1 else GRAY_LT if rank == 2 else (205, 127, 50) if rank == 3 else GRAY_DK
        draw.text((12, y+10), f"#{rank:02}", font=f_row, fill=rank_col)
        draw.text((58, y+10), name, font=f_row, fill=WHITE)

        stats_str = f"{streak} streak  ·  {format_num(wins)} wins  ·  {wr}% WR"
        sw, _ = text_size(draw, stats_str, f_sub)
        draw.text((W-sw-14, y+11), stats_str, font=f_sub, fill=gm_color)
        draw.line([(0, y+35), (W, y+35)], fill=(20, 25, 38), width=1)

    footer_text = f"SNIPER 3.0  ·  {_footer_ts()}"
    draw.rectangle([0, H-20, W, H], fill=(8, 10, 15))
    draw_gradient_bar(img, 0, H-20, W, 1, ACCENT_PURP, gm_color)
    fw, _ = text_size(draw, footer_text, f_small)
    draw.text((W-fw-10, H-14), footer_text, font=f_small, fill=GRAY_DK)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
