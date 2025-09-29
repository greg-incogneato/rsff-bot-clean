# app.py — RSFF Bot v0.1.1 (clean)

APP_VERSION = "v0.1.1"

import os, base64, tempfile, logging, resource, psutil
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

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
    await ctx.send("\n".join([
        "**RSFF Bot — Commands**",
        "`!cap [team]` — Cap used/remaining. Omit team to use your mapped team.",
        "`!capdetail [team]` — Top counted salaries + who got DP.",
        "`!add <player>` — Sim add to your team (discount after week).",
        "`!drop <player>` — Sim drop from your team (dead cap applies).",
        "`!leaders` — Top 5 cap space remaining.",
        "`!status` — Snapshot/time and row counts.",
        "`!statusmem` — Process memory + tab counts.",
        "`!sync` — Admin only: refresh from Google Sheet.",
    ]))

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
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.`discord user`, or run `!cap <team>` once.")
    res = simulate_drop(SNAPSHOT, team, player)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")
    lines = [
        f"**Drop {res['player']}** for **{res['team']}**",
        f"Dead Cap: `${res['dead_cap']:,.0f}`",
        f"Roster: {res['roster_before']} → {res['roster_after']} (max {14})",
    ]
    for v in res.get("violations", []):
        lines.append(f"⚠️ {v['code']}: {v['detail']}")
    lines.append(f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
    await ctx.send("\n".join(lines))

@bot.command(name="add")
async def add_cmd(ctx, *, player: str):
    team = resolve_user_team(SNAPSHOT, ctx.author)
    if not team:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.`discord user`, or run `!cap <team>` once.")
    res = simulate_add(SNAPSHOT, team, player)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")
    disc = f" (discounted {int(res['discount_applied']*100)}%)" if res.get("discount_applied", 0) > 0 else ""
    lines = [
        f"**Add {res['player']}** to **{res['team']}**",
        f"Salary: `${res['salary_effective']:,.0f}`{disc} (base `${res['salary_base']:,.0f}`)",
        f"Roster: {res['roster_before']} → {res['roster_after']} (max {14})",
    ]
    for v in res.get("violations", []):
        lines.append(f"⚠️ {v['code']}: {v['detail']}")
    lines.append(f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
    await ctx.send("\n".join(lines))

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing")
    bot.run(DISCORD_TOKEN)