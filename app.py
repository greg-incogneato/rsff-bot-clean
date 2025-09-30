# app.py — RSFF Bot v0.1.2 (clean)

APP_VERSION = "v0.1.2"

import os, base64, tempfile, logging, resource, psutil
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from sim.team_summary import team_summary
from sim.player_lookup import player_lookup
from sim.ops import simulate_add, simulate_drop, simulate_whatif

# ---- Load env FIRST
load_dotenv()
BOT_ENV = os.getenv("BOT_ENV", "prod")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
SHEET_ID = os.getenv("RSFF_SHEET_ID", "")
RANGES = [r.strip() for r in os.getenv("RSFF_RANGES", "").split(",") if r.strip()]

# ---- Optional: base64 SA shim
b64 = os.getenv("GCP_SA_JSON_BASE64")
if b64 and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "wb") as f:
        f.write(base64.b64decode(b64))
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

# ---- Imports that rely on env (after shim)
from sheets_sync import pull_snapshot
from sim.cap import cap_summary, cap_detail
from sim.ops import simulate_add, simulate_drop

# ---- Bot intents and creation (BEFORE any decorators)
intents = discord.Intents.none()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    max_messages=100,
    chunk_guilds_at_startup=False,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rsff")
log.info(f"bot.intents.message_content={bot.intents.message_content} BOT_ENV={BOT_ENV} GUILD_ID={DISCORD_GUILD_ID}")

SNAPSHOT = None

# ---- Background sync
@tasks.loop(minutes=30)
async def autosync():
    global SNAPSHOT
    try:
        SNAPSHOT = pull_snapshot(SHEET_ID, RANGES)
        log.info(f"⏱️ autosync → {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}")
    except Exception as e:
        log.error(f"autosync failed: {e}")

@autosync.before_loop
async def before_autosync():
    await bot.wait_until_ready()

# ---- Lifecycle
@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(name=f"RSFF {BOT_ENV} {APP_VERSION} — !help"))
    global SNAPSHOT
    SNAPSHOT = pull_snapshot(SHEET_ID, RANGES)

    try:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"✅ Slash commands synced to guild {DISCORD_GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"✅ Slash commands globally synced ({len(synced)} cmds)")
    except Exception as e:
        print(f"Slash sync failed: {e}")

    autosync.start()
    print(f"✅ Logged in as {bot.user} | Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}")
    print(f"Bot user: {bot.user} id={bot.user.id} ENV={BOT_ENV}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"⚠️ {type(error).__name__}: {error}")

# ---- Debug helpers
@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("pong (!)")

@bot.tree.command(name="ping", description="Ping test")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong (/)—ephemeral", ephemeral=True)

@bot.command(name="statusmem")
async def statusmem_cmd(ctx):
    rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    tabs = SNAPSHOT.get("tabs", {})
    lines = [
        f"Memory RSS: `{rss_mb:,.0f} MB`",
        "Tabs: " + ", ".join([f"{k}:{len(v)}" for k, v in tabs.items()]),
        f"Snapshot `{SNAPSHOT['hash']}` @ {SNAPSHOT['ts']}",
    ]
    await ctx.send("\n".join(lines))

@bot.tree.command(name="help", description="Show RSFF Bot commands")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message("\n".join([
        "**RSFF Bot — Commands**",
        "`!cap [team]` — Cap used/remaining. Omit team to use your mapped team.",
        "`!capdetail [team]` — Top counted salaries + who got DP.",
        "`!add <player>` — Sim add to your team (discount after week).",
        "`!drop <player>` — Sim drop from your team (dead cap applies).",
        "`!leaders` — Top 5 cap space remaining.",
        "`!status` — Snapshot/time and row counts.",
        "`!statusmem` — Process memory + tab counts.",
        "`!sync` — Admin only: refresh from Google Sheet.",
    ]), ephemeral=True)

@bot.command(name="help")
async def help_cmd(ctx):
    lines = [
        "**RSFF Bot — Commands**",
        "",
        "__Cap & Team__",
        "`!cap [team]` — Cap used/remaining. Omit team to use your mapped team.",
        "`!capdetail [team]` — Top counted salaries + DP/IR relief shown.",
        "`!teamsum [team]` — Full team summary (net used, gross, DP, IR, players).",
        "",
        "__Players__",
        "`!player <name>` — Player info: AAV, NFL team, bye, rostered-by, DP/IR, Sleeper ID.",
        "",
        "__Transactions (simulated)__",
        "`!add <player>` — Sim add. Shows roster change and cap impact (before → after, Δ).",
        "`!drop <player>` — Sim drop. Applies dead-cap from rules and shows cap impact.",
        "`!whatif add <p1> [drop <p2>]` — Combined scenario with DP re-selection.",
        "    e.g., `!whatif add aaron rodgers drop mahomes`",
        "",
        "__Leaders & Status__",
        "`!leaders` — Top cap space remaining (Top 5).",
        "`!status` — Snapshot hash/time and row counts.",
        "`!version` — Bot version + snapshot.",
        "",
        "__Admin__",
        "`!sync` — Admin only: refresh from Google Sheets.",
        "",
        "_Notes:_",
        "• Team defaulting uses your Discord handle mapped in `Owners2025.discord user`.",
        "• Adds don’t hard-block at roster max; you’ll see a warning to drop someone.",
        "• Cap math follows RSFF rules: DP/IR relief and dead-cap on drops.",
        f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_",
    ]
    await ctx.send("\n".join(lines))
    
# ---- Team resolution helpers + core commands
def _norm(s: str) -> str:
    return (s or "").strip()

def resolve_user_team(snapshot, member) -> str | None:
    tabs = snapshot.get("tabs", {})
    owners = tabs.get("Owners2025", [])
    rosters = tabs.get("Rosters", [])
    cand = {
        str(member),
        getattr(member, "display_name", "") or "",
        getattr(member, "global_name", "") or "",
        getattr(member, "name", "") or "",
    }
    cand = {c for c in (_norm(c) for c in cand) if c}
    for o in owners:
        for key in ("discord user", "discord_user", "owner_display", "display_name", "team_name"):
            v = _norm(str(o.get(key, "")))
            if v and any(v.lower() == c.lower() for c in cand):
                return o.get("team_name") or o.get("display_name") or o.get("owner_display") or v
    teams = {_norm(str(r.get("Team") or r.get("team") or "")) for r in rosters}
    for c in cand:
        if c in teams:
            return c
    return None

@bot.command(name="version")
async def version_cmd(ctx):
    await ctx.send(f"RSFF Bot {APP_VERSION} | Snapshot {SNAPSHOT['hash']}")

@bot.command(name="status")
async def status_cmd(ctx):
    tabs = SNAPSHOT.get("tabs", {})
    counts = {k: len(v) for k, v in tabs.items()}
    await ctx.send("\n".join([
        f"Snapshot `{SNAPSHOT['hash']}` @ {SNAPSHOT['ts']}",
        "Rows → " + ", ".join([f"{k}:{v}" for k, v in counts.items()])
    ]))

@bot.command(name="leaders")
@commands.cooldown(2, 10, commands.BucketType.user)
async def leaders_cmd(ctx, what: str = "cap"):
    if what.lower() not in ("cap", "capspace", "space"):
        return await ctx.send("Try `!leaders` (cap space leaders).")
    owners = SNAPSHOT.get("tabs", {}).get("Owners2025", [])
    rows, seen = [], set()
    for o in owners:
        q = (o.get("team_name") or o.get("display_name") or o.get("owner_display") or o.get("discord user") or "").strip()
        if not q or q in seen: continue
        seen.add(q)
        try:
            res = cap_summary(SNAPSHOT, q)
            rows.append((res["cap_remaining"], res["team_name"], res["cap_used"], res["cap_limit"]))
        except Exception:
            continue
    rows.sort(reverse=True)
    if not rows: return await ctx.send("No teams found.")
    header = f"**Cap Space Leaders (Top 5)**\n_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_"
    lines = [header] + [f"• **{n}** → Remaining `${r:,.0f}` (Used `${u:,.0f}` / `${L:,.0f}`)" for r,n,u,L in rows[:5]]
    await ctx.send("\n".join(lines))

@bot.command(name="cap")
@commands.cooldown(2, 10, commands.BucketType.user)
async def cap_cmd(ctx, *, team_name: str | None = None):
    query = team_name or resolve_user_team(SNAPSHOT, ctx.author)
    if not query:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.`discord user`, or run `!cap <team>` once.")
    res = cap_summary(SNAPSHOT, query)
    lines = [
        f"**{res['team_name']}**",
        f"Cap Used: `${res['cap_used']:,.0f}` / `${res['cap_limit']:,.0f}`",
    ]
    if res.get("dp_relief", 0) > 0:
        who = f" ({res['dp_player']})" if res.get("dp_player") else ""
        lines.append(f"DP Relief: `-${res['dp_relief']:,.0f}`{who}")
    lines += [
        f"Remaining: `${res['cap_remaining']:,.0f}`",
        f"Players Counted: {res['players_counted']}",
        f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_",
    ]
    await ctx.send("\n".join(lines))

@bot.command(name="capdetail")
@commands.cooldown(2, 10, commands.BucketType.user)
async def capdetail_cmd(ctx, *, team_name: str | None = None):
    query = team_name or resolve_user_team(SNAPSHOT, ctx.author)
    if not query:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.`discord user`, or run `!capdetail <team>` once.")
    try:
        det = cap_detail(SNAPSHOT, query, top_n=8)
        lines = [
            f"**{det['team_name']} — Cap Detail**",
            f"Used `${det['cap_used']:,.0f}` / `${det['cap_limit']:,.0f}` | Remaining `${det['cap_remaining']:,.0f}`",
        ]
        if det.get("dp_relief", 0) > 0:
            lines.append(f"DP Relief: `-${det['dp_relief']:,.0f}` ({det['dp_player']})")
        lines.append("**Top salaries counted:**")
        for p in det["top"]:
            dp_tag = " (DP)" if p["name"] == det.get("dp_player") else ""
            lines.append(f"• {p['name']} {p['pos'] or ''} — `${p['salary']:,.0f}`{dp_tag}")
        lines.append(f"_Players counted: {det['total_counted']} · Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
        await ctx.send("\n".join(lines))
    except Exception as e:
        await ctx.send(f"❌ {e}")

@bot.command(name="sync")
@commands.has_guild_permissions(administrator=True)
async def sync_cmd(ctx):
    global SNAPSHOT
    before = {k: len(v) for k, v in (SNAPSHOT or {"tabs": {}}).get("tabs", {}).items()}
    SNAPSHOT = pull_snapshot(SHEET_ID, RANGES)
    after = {k: len(v) for k, v in SNAPSHOT.get("tabs", {}).items()}
    keys = sorted(set(before) | set(after))
    diffs = []
    for k in keys:
        b, a = before.get(k, 0), after.get(k, 0)
        mark = "↔️" if a == b else ("⬆️" if a > b else "⬇️")
        diffs.append(f"{k}:{b}→{a} {mark}")
    await ctx.send("🔄 Synced.\n" f"Snapshot `{SNAPSHOT['hash']}` @ {SNAPSHOT['ts']}\n" "Rows: " + ", ".join(diffs))

@sync_cmd.error
async def sync_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ `!sync` is admin-only.")

@bot.command(name="drop")
async def drop_cmd(ctx, *, player: str):
    team = resolve_user_team(SNAPSHOT, ctx.author)
    if not team:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.discord user, or run `!cap <team>` once.")
    res = simulate_drop(SNAPSHOT, team, player)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")

    ts = team_summary(SNAPSHOT, team)
    used_before = float(ts["cap_used"])
    cap_limit   = float(ts["cap_limit"])
    rem_before  = cap_limit - used_before

    base = float(res["salary_base"])
    dead = float(res["dead_cap"])
    was_dp = bool(res.get("was_dp", False))
    was_ir = bool(res.get("was_ir", False))

    if was_dp or was_ir:
        used_after = used_before + dead
    else:
        used_after = used_before - base + dead

    rem_after  = cap_limit - used_after
    delta_used = used_after - used_before
    delta_rem  = rem_after - rem_before

    lines = [
        f"**Drop {res['player']}** for **{res['team']}**",
        f"Dead Cap (from rules): `${dead:,.0f}` on base `${base:,.0f}`",
        f"Roster: {res['roster_before']} → {res['roster_after']} (max {14})",
        "",
        f"**Cap Used:** `${used_before:,.0f}` → `${used_after:,.0f}`  _(Δ `${delta_used:,.0f}`)_",
        f"**Cap Remaining:** `${rem_before:,.0f}` → `${rem_after:,.0f}`  _(Δ `${delta_rem:,.0f}`)_",
        f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_",
    ]
    await ctx.send("\n".join(lines))

@bot.command(name="add")
async def add_cmd(ctx, *, player: str):
    team = resolve_user_team(SNAPSHOT, ctx.author)
    if not team:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.discord user, or run `!cap <team>` once.")
    res = simulate_add(SNAPSHOT, team, player)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")

    # Baseline from snapshot
    ts = team_summary(SNAPSHOT, team)
    used_before = float(ts["cap_used"])
    cap_limit   = float(ts["cap_limit"])
    rem_before  = cap_limit - used_before

    used_after = used_before + float(res["salary_effective"])
    rem_after  = cap_limit - used_after
    delta_used = used_after - used_before
    delta_rem  = rem_after - rem_before

    lines = [
        f"**Add {res['player']}** to **{res['team']}**",
        f"Availability: {res.get('availability','FA')}",
        f"Salary: `${res['salary_effective']:,.0f}` (base `${res['salary_base']:,.0f}`)",
        f"Roster: {res['roster_before']} → {res['roster_after']} (max {14})",
        "",
        f"**Cap Used:** `${used_before:,.0f}` → `${used_after:,.0f}`  _(Δ `${delta_used:,.0f}`)_",
        f"**Cap Remaining:** `${rem_before:,.0f}` → `${rem_after:,.0f}`  _(Δ `${delta_rem:,.0f}`)_",
    ]
    for v in res.get("violations", []):
        lines.append(f"⚠️ {v['code']}: {v['detail']}")
    lines.append(f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
    await ctx.send("\n".join(lines))

@bot.command(name="teamsum")
async def teamsum_cmd(ctx, *, team_name: str | None = None):
    query = team_name or resolve_user_team(SNAPSHOT, ctx.author)
    if not query:
        return await ctx.send("❓ Couldn’t map you to a team. Try `!teamsum <team>`.")

    res = team_summary(SNAPSHOT, query)

    lines = [
        f"**Team: {res['team_name']}**",
        f"Cap Used (net): `${res['cap_used']:,.0f}` / `${res['cap_limit']:,.0f}`",
        f"Breakdown: Gross `${res['gross_cap']:,.0f}` – DP `${res['dp_relief']:,.0f}` – IR `${res['ir_relief']:,.0f}`",
        f"Cap Remaining: `${res['cap_remaining']:,.0f}`",
        f"Players Counted: {res['players_counted']} / 14",
        f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_",
        "",
        "**Active Roster:**",
    ]

    for p in res["active"]:
        dp_tag = " (DP)" if p["dp"] else ""
        lines.append(f"{p['pos']:<4} {p['name']} — `${p['salary']:,.0f}`{dp_tag}")

    if res["ir"]:
        lines += [
            "",
            f"**Injured Reserve ({len(res['ir'])} players — IR Relief: -${res['ir_relief']:,.0f})**"
        ]
        for p in res["ir"]:
            lines.append(f"{p['pos']:<4} {p['name']} — `${p['salary']:,.0f}` (IR)")

    await ctx.send("\n".join(lines))

@bot.command(name="player")
async def player_cmd(ctx, *, name: str):
    res = player_lookup(SNAPSHOT, name)
    if not res:
        return await ctx.send(f"❌ No match for `{name}`. Try more letters (e.g., `!player patrick maho`).")

    status = "Free Agent" if res["status"] == "FA" else f"Rostered by **{res['rostered_by']}**"
    flags = []
    if res.get("dp"): flags.append("DP")
    if res.get("on_ir"): flags.append("IR")
    flag_txt = f" ({', '.join(flags)})" if flags else ""

    lines = [
        f"**{res['name']}** — {res.get('pos') or '?'} {res.get('nfl') or ''}{flag_txt}",
        f"AAV: `${res['aav']:,.0f}` | Status: {status}",
    ]
    if res.get("player_id"):
        lines.append(f"Sleeper ID: `{res['player_id']}`")
    if res.get("bye"):
        lines.append(f"Bye: {res['bye']}")
    lines.append(f"_Search match: {res.get('match_score', 0)}/100 · Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
    await ctx.send("\n".join(lines))

@bot.command(name="whatif")
async def whatif_cmd(ctx, *, args: str):
    """
    Usage examples:
      !whatif add aaron rodgers
      !whatif drop mahomes
      !whatif add aaron rodgers drop mahomes
    """
    team = resolve_user_team(SNAPSHOT, ctx.author)
    if not team:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.discord user, or run `!cap <team>` once.")

    # very light parser
    tokens = args.split()
    add_query, drop_query = None, None
    i = 0
    while i < len(tokens):
        t = tokens[i].lower()
        if t == "add":
            i += 1
            start = i
            while i < len(tokens) and tokens[i].lower() not in {"add", "drop"}:
                i += 1
            add_query = " ".join(tokens[start:i]).strip()
            continue
        if t == "drop":
            i += 1
            start = i
            while i < len(tokens) and tokens[i].lower() not in {"add", "drop"}:
                i += 1
            drop_query = " ".join(tokens[start:i]).strip()
            continue
        i += 1

    if not add_query and not drop_query:
        return await ctx.send("Try: `!whatif add <player>` or `!whatif drop <player>` or `!whatif add <p1> drop <p2>`")

    res = simulate_whatif(SNAPSHOT, team, add_query, drop_query)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")

    ts = team_summary(SNAPSHOT, team)
    used_before = float(ts["cap_used"])
    cap_limit   = float(ts["cap_limit"])
    rem_before  = cap_limit - used_before

    used_after = used_before + float(res["used_delta"])
    rem_after  = cap_limit - used_after
    delta_used = used_after - used_before
    delta_rem  = rem_after - rem_before

    lines = [f"**What-if for {team}**"]
    if add_query:
        ar = res.get("add_result")
        if ar and ar.get("status") == "OK":
            lines += [
                f"• Add **{ar['player']}** → Salary `${ar['salary_effective']:,.0f}` (base `${ar['salary_base']:,.0f}`)",
            ]
        elif ar:
            lines += [f"• Add {add_query}: ❌ {ar.get('reason')}"]

    if drop_query:
        dr = res.get("drop_result")
        if dr and dr.get("status") == "OK":
            lines += [
                f"• Drop **{dr['player']}** → Dead Cap `${dr['dead_cap']:,.0f}` on base `${dr['salary_base']:,.0f}`",
            ]
        elif dr:
            lines += [f"• Drop {drop_query}: ❌ {dr.get('reason')}"]

    if res.get("violations"):
        for v in res["violations"]:
            lines.append(f"⚠️ {v['code']}: {v['detail']}")

    if res.get("dp_before") is not None and res.get("dp_after") is not None:
        if abs(res["dp_after"] - res["dp_before"]) > 1e-6:
            lines.append(f"_DP re-selected: `${res['dp_before']:,.0f}` → `${res['dp_after']:,.0f}`_")

    lines += [
        "",
        f"**Cap Used:** `${used_before:,.0f}` → `${used_after:,.0f}`  _(Δ `${delta_used:,.0f}`)_",
        f"**Cap Remaining:** `${rem_before:,.0f}` → `${rem_after:,.0f}`  _(Δ `${delta_rem:,.0f}`)_",
        f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_",
    ]
    await ctx.send("\n".join(lines))

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing")
    bot.run(DISCORD_TOKEN)