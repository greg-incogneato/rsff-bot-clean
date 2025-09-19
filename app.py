APP_VERSION = "v0.1.0"
import os, discord
from discord.ext import commands
from dotenv import load_dotenv
from sheets_sync import pull_snapshot
from sim.cap import cap_summary
from discord.ext import commands, tasks
from rapidfuzz import process, fuzz
from sim.ops import simulate_add, simulate_drop  # top-level imports
from sim.cap import cap_summary, cap_detail
import base64, tempfile, os

# --- Team resolution helpers ---
def _norm(s: str) -> str:
    return (s or "").strip()

def resolve_user_team(snapshot, member):
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
        for key in ("discord user","discord_user","owner_display","display_name","team_name"):
            v = _norm(str(o.get(key, "")))
            if v and any(v.lower() == c.lower() for c in cand):
                return (o.get("team_name") or o.get("display_name") or o.get("owner_display") or v)

    teams = {_norm(str(r.get("Team") or r.get("team") or "")) for r in rosters}
    for c in cand:
        if c in teams:
            return c
    return None

def _rules_dict(rows):
    d = {}
    for r in rows or []:
        k = str(r.get("key") or r.get("Key") or "").strip()
        v = str(r.get("value") or r.get("Value") or "").strip()
        if not k:
            # tolerate 1-col rows
            if len(r.keys()) == 1:
                k, v = list(r.items())[0]
            else:
                continue
        if v.upper() in ("TRUE","FALSE"):
            d[k] = (v.upper()=="TRUE")
        else:
            try:
                d[k] = float(v) if "." in v else int(v)
            except:
                d[k] = v
    return d

load_dotenv()
gcp_b64 = os.getenv("GCP_SA_JSON_BASE64")
if gcp_b64 and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.write(base64.b64decode(gcp_b64))
    tmp.flush()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
TOKEN = os.getenv("DISCORD_TOKEN")
SHEET_ID = os.getenv("RSFF_SHEET_ID")
RANGES = os.getenv("RSFF_RANGES").split(",")

intents = discord.Intents.none()     # start from nothing
intents.message_content = True       # only what you need

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    max_messages=100,                # default is 1000; reduce cache
    chunk_guilds_at_startup=False,   # don’t prefetch members
)

SNAPSHOT = None
@tasks.loop(minutes=30)
async def autosync():
    global SNAPSHOT
    try:
        SNAPSHOT = pull_snapshot(SHEET_ID, RANGES)
        # optional: print to console instead of spamming Discord
        print(f"⏱️ autosync → {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}")
    except Exception as e:
        print(f"autosync failed: {e}")

@autosync.before_loop
async def before_autosync():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(name=f"RSFF {APP_VERSION} — !help"))
    global SNAPSHOT
    SNAPSHOT = pull_snapshot(SHEET_ID, RANGES)
    autosync.start()
    print(f"✅ Logged in as {bot.user} | Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}")    

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # ignore fat-fingers, or reply with !help
    await ctx.send(f"⚠️ {type(error).__name__}: {error}")

@bot.command(name="version")
async def version_cmd(ctx):
    await ctx.send(f"RSFF Bot {APP_VERSION} | Snapshot {SNAPSHOT['hash']}")

@bot.command(name="sync")
@commands.has_guild_permissions(administrator=True)
async def sync_cmd(ctx):
    global SNAPSHOT
    SNAPSHOT = pull_snapshot(SHEET_ID, RANGES)
    await ctx.send(f"🔄 Synced. Snapshot `{SNAPSHOT['hash']}` @ {SNAPSHOT['ts']}")

@sync_cmd.error
async def sync_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ `!sync` is admin-only.")

@bot.command(name="cap")
@commands.cooldown(2, 10, commands.BucketType.user)  # 2 uses per 10s per user
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
@commands.cooldown(2, 10, commands.BucketType.user)  # 2 uses per 10s per user
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

@bot.command(name="help")
async def help_cmd(ctx):
    lines = [
        "**RSFF Bot — Commands**",
        "`!cap [team]` — Cap used/remaining. Omit team to use your mapped team.",
        "`!capdetail [team]` — Top counted salaries + who got DP.",
        "`!add <player>` — Sim add to your team (discount after week).",
        "`!drop <player>` — Sim drop from your team (dead cap applies).",
        "`!leaders` — Top 5 cap space remaining.",
        "`!status` — Snapshot/time and row counts.",
        "`!sync` — Admin only: refresh from Google Sheet.",
    ]
    await ctx.send("\n".join(lines))

@bot.command(name="status")
async def status_cmd(ctx):
    tabs = SNAPSHOT.get("tabs", {})
    counts = {k: len(v) for k, v in tabs.items()}
    lines = [
        f"Snapshot `{SNAPSHOT['hash']}` @ {SNAPSHOT['ts']}",
        f"Rows → " + ", ".join([f"{k}:{v}" for k, v in counts.items()])
    ]
    await ctx.send("\n".join(lines))

@bot.command(name="leaders")
@commands.cooldown(2, 10, commands.BucketType.user)  # 2 uses per 10s per user
async def leaders_cmd(ctx, what: str = "cap"):
    """Usage: !leaders  (defaults to cap) """
    if what.lower() not in ("cap", "capspace", "space"):
        return await ctx.send("Try `!leaders` (cap space leaders).")
    tabs = SNAPSHOT.get("tabs", {})
    owners = tabs.get("Owners2025", [])
    lines = []
    rows = []
    # compute per owner using your existing cap_summary
    seen = set()
    for o in owners:
        q = (o.get("team_name") or o.get("display_name") or o.get("owner_display") or o.get("discord user") or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        try:
            res = cap_summary(SNAPSHOT, q)
            rows.append((res["cap_remaining"], res["team_name"], res["cap_used"], res["cap_limit"]))
        except:
            continue
    rows.sort(reverse=True)  # highest remaining first
    top = rows[:5]
    for rem, name, used, lim in top:
        lines.append(f"• **{name}** → Remaining `${rem:,.0f}` (Used `${used:,.0f}` / `${lim:,.0f}`)")
    if not lines:
        lines = ["No teams found."]
    header = f"**Cap Space Leaders (Top 5)**\n_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_"
    await ctx.send("\n".join([header] + lines))

@bot.command(name="drop")
async def drop_cmd(ctx, *, player: str):
    team = resolve_user_team(SNAPSHOT, ctx.author)
    if not team:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.discord user, or run `!cap <team>` once.")
    res = simulate_drop(SNAPSHOT, team, player)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")
    rules = _rules_dict(SNAPSHOT.get("tabs", {}).get("Rules", []))
    roster_max = int(rules.get("roster_max", 14))
    lines = [
        f"**Drop {res['player']}** for **{res['team']}**",
        f"Dead Cap: `${res['dead_cap']:,.0f}`",
        f"Roster: {res['roster_before']} → {res['roster_after']} (max {roster_max})",
    ]
    for v in res.get("violations", []):
        lines.append(f"⚠️ {v['code']}: {v['detail']}")
    lines.append(f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
    await ctx.send("\n".join(lines))

@bot.command(name="add")
async def add_cmd(ctx, *, player: str):
    team = resolve_user_team(SNAPSHOT, ctx.author)
    if not team:
        return await ctx.send("❓ I couldn't map you to a team. Add your handle to Owners2025.discord user, or run `!cap <team>` once.")
    res = simulate_add(SNAPSHOT, team, player)
    if res["status"] == "INVALID":
        return await ctx.send(f"❌ {res['reason']}")
    rules = _rules_dict(SNAPSHOT.get("tabs", {}).get("Rules", []))
    roster_max = int(rules.get("roster_max", 14))
    disc = f" (discounted {int(res['discount_applied']*100)}%)" if res.get("discount_applied",0)>0 else ""
    lines = [
        f"**Add {res['player']}** to **{res['team']}**",
        f"Salary: `${res['salary_effective']:,.0f}`{disc} (base `${res['salary_base']:,.0f}`)",
        f"Roster: {res['roster_before']} → {res['roster_after']} (max {roster_max})",
    ]
    for v in res.get("violations", []):
        lines.append(f"⚠️ {v['code']}: {v['detail']}")
    lines.append(f"_Snapshot {SNAPSHOT['hash']} @ {SNAPSHOT['ts']}_")
    await ctx.send("\n".join(lines))


bot.run(TOKEN)