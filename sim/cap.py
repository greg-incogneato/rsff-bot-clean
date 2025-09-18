# sim/cap.py  (NO imports from .cap at the top)

def _num(x):
    s = str(x or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except:
        return 0.0

def _get(rec, *keys):
    for k in keys:
        v = rec.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""

def _rules_dict(rules_rows):
    d = {}
    for r in rules_rows:
        # support {key,value} rows or single-key rows
        k = _get(r, "key", "Key")
        v = _get(r, "value", "Value")
        if not k:
            if len(r.keys()) == 1:
                k, v = list(r.items())[0]
            else:
                continue
        vs = str(v)
        if vs.upper() in ("TRUE", "FALSE"):
            d[k] = (vs.upper() == "TRUE")
        else:
            try:
                d[k] = float(vs) if "." in vs else int(vs)
            except:
                d[k] = vs
    return d

def cap_summary(snapshot, team_query: str):
    tabs      = snapshot["tabs"]
    salaries  = tabs.get("Salary2025", [])
    rosters   = tabs.get("Rosters", [])
    owners    = tabs.get("Owners2025", [])
    rules     = _rules_dict(tabs.get("Rules", []))

    tq = team_query.strip().lower()

    # --- resolve team label from Owners or fall back to Rosters.Team ---
    owner_row = None
    for o in owners:
        name = _get(o, "team_name", "display_name", "owner_display", "discord user", "discord_user")
        if name and tq in name.lower():
            owner_row = o
            break

    roster_team = None
    if not owner_row:
        teams_seen = {_get(r, "Team", "team") for r in rosters if _get(r, "Team", "team")}
        for t in teams_seen:
            if tq in t.lower():
                roster_team = t
                break
        if not roster_team:
            raise ValueError(f"Team '{team_query}' not found.")

    team_label = (
        _get(owner_row, "team_name")
        or _get(owner_row, "display_name")
        or _get(owner_row, "owner_display")
        or _get(owner_row, "discord user", "discord_user")
        or roster_team
        or team_query
    )

    # --- rules ---
    cap_limit            = float(rules.get("cap_limit") or 0)
    dp_enabled           = bool(rules.get("dp_enabled", True))
    dp_relief_pct        = float(rules.get("dp_relief_pct", 1.0))
    dp_auto_highest      = bool(rules.get("dp_auto_highest_if_unset", True))

    # --- salary index ---
    sal_by_pid, sal_by_name = {}, {}
    for s in salaries:
        pid = _get(s, "sleeper_player_id", "yahoo_player_id")
        nm  = _get(s, "player_name")
        sal = _num(_get(s, "cap_hit_2025", "aav"))
        if pid: sal_by_pid[pid] = sal
        if nm:  sal_by_name[nm.lower()] = sal

    used = 0.0
    counted = 0
    dp_candidates = []   # (salary, player_name)
    all_active    = []   # (salary, player_name)

    for r in rosters:
        r_team = _get(r, "Team", "team")
        if r_team != team_label:
            continue

        on_roster = (_get(r, "On Roster Flag", "on_roster_flag") or "TRUE").upper() == "TRUE"
        on_ir     = (_get(r, "On IR?", "on_ir?", "on_ir") or "FALSE").upper() == "TRUE"
        if not on_roster or on_ir:
            continue

        pid    = _get(r, "Player ID", "player_id")
        pname  = _get(r, " Player Name", "player_name") or "Unknown"
        salary = sal_by_pid.get(pid) or sal_by_name.get(pname.lower())
        if salary is None:
            salary = _num(_get(r, "AAV"))

        used += (salary or 0.0)
        counted += 1

        is_dp = (_get(r, "DP?", "dp?") or "FALSE").upper() == "TRUE"
        if salary and salary > 0:
            all_active.append((salary, pname))
            if is_dp:
                dp_candidates.append((salary, pname))


    # --- apply DP relief ---
    dp_relief = 0.0
    dp_name   = None
    if dp_enabled:
        pick = None
        if dp_candidates:
            pick = max(dp_candidates, key=lambda x: x[0])
        elif dp_auto_highest and all_active:
            pick = max(all_active, key=lambda x: x[0])
        if pick:
            dp_salary, dp_name = pick
            dp_relief = dp_salary * dp_relief_pct
            used -= dp_relief

    remaining = cap_limit - used

    return {
        "team_name": team_label,
        "cap_limit": round(cap_limit, 2),
        "cap_used": round(used, 2),
        "cap_remaining": round(remaining, 2),
        "players_counted": counted,
        "dp_relief": round(dp_relief, 2),
        "dp_player": dp_name,
    }

def cap_detail(snapshot, team_query: str, top_n: int = 8):
    """Return the players counted toward cap (after IR filter), sorted by salary desc,
       plus which player received DP relief."""
    tabs      = snapshot["tabs"]
    salaries  = tabs.get("Salary2025", [])
    rosters   = tabs.get("Rosters", [])
    rules     = _rules_dict(tabs.get("Rules", []))

    # reuse team resolution by calling cap_summary once
    base = cap_summary(snapshot, team_query)
    team_label = base["team_name"]

    # salary index
    sal_by_pid, sal_by_name = {}, {}
    for s in salaries:
        pid = _get(s, "sleeper_player_id", "yahoo_player_id")
        nm  = _get(s, "player_name")
        sal = _num(_get(s, "cap_hit_2025", "aav"))
        if pid: sal_by_pid[pid] = sal
        if nm:  sal_by_name[nm.lower()] = sal

    # collect counted rows
    counted = []
    for r in rosters:
        if _get(r, "Team", "team") != team_label:
            continue
        on_roster = (_get(r, "On Roster Flag", "on_roster_flag") or "TRUE").upper() == "TRUE"
        on_ir     = (_get(r, "On IR?", "on_ir?", "on_ir") or "FALSE").upper() == "TRUE"
        if not on_roster or on_ir:
            continue
        pid    = _get(r, "Player ID", "player_id")
        pname  = _get(r, " Player Name", "player_name") or "Unknown"
        pos    = _get(r, " Pos", "pos")
        sal    = sal_by_pid.get(pid) or sal_by_name.get(pname.lower())
        if sal is None:
            sal = _num(_get(r, "AAV"))
        is_dp  = (_get(r, "DP?", "dp?") or "FALSE").upper() == "TRUE"
        counted.append({"name": pname, "pos": pos, "salary": sal or 0.0, "dp": is_dp})

    # sort by salary desc
    counted.sort(key=lambda x: x["salary"], reverse=True)

    # figure out DP target (matches cap_summary logic: flagged highest else auto-highest)
    dp_relief_pct   = float(rules.get("dp_relief_pct", 1.0))
    dp_enabled      = bool(rules.get("dp_enabled", True))
    dp_auto_highest = bool(rules.get("dp_auto_highest_if_unset", True))

    dp_pick = None
    if dp_enabled:
        flagged = [c for c in counted if c["dp"] and c["salary"] > 0]
        if flagged:
            dp_pick = max(flagged, key=lambda x: x["salary"])
        elif dp_auto_highest and counted:
            dp_pick = counted[0]

    return {
        "team_name": team_label,
        "cap_limit": base["cap_limit"],
        "cap_used": base["cap_used"],
        "cap_remaining": base["cap_remaining"],
        "dp_player": dp_pick["name"] if dp_pick else None,
        "dp_relief": (dp_pick["salary"] * dp_relief_pct) if dp_pick else 0.0,
        "top": counted[:top_n],
        "total_counted": len(counted),
    }