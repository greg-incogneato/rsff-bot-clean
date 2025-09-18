# sim/ops.py
from .cap import _num, _get, _rules_dict, cap_summary

def _build_indexes(snapshot):
    tabs = snapshot["tabs"]
    salaries = tabs.get("Salary2025", [])
    rosters  = tabs.get("Rosters", [])
    rules    = _rules_dict(tabs.get("Rules", []))

    by_pid, by_name = {}, {}
    for s in salaries:
        pid = _get(s, "sleeper_player_id", "yahoo_player_id")
        nm  = _get(s, "player_name")
        sal = _num(_get(s, "cap_hit_2025", "aav"))
        if pid: by_pid[pid] = sal
        if nm:  by_name[nm.lower()] = sal
    return tabs, by_pid, by_name, rules, rosters

def _salary_for_row(by_pid, by_name, row):
    pid   = _get(row, "Player ID", "player_id")
    pname = _get(row, " Player Name", "player_name")
    sal   = by_pid.get(pid) or by_name.get(pname.lower())
    if sal is None:
        sal = _num(_get(row, "AAV"))
    return sal or 0.0, (pname or pid or "Unknown")

def _active_count(rosters, team_label):
    return sum(
        1 for r in rosters
        if _get(r, "Team", "team") == team_label
        and (_get(r, "On Roster Flag", "on_roster_flag") or "TRUE").upper() == "TRUE"
        and (_get(r, "On IR?", "on_ir?", "on_ir") or "FALSE").upper() != "TRUE"
    )

def simulate_drop(snapshot, team_query: str, player_query: str):
    tabs, by_pid, by_name, rules, rosters = _build_indexes(snapshot)
    team_label = cap_summary(snapshot, team_query)["team_name"]

    dead_cap_pct = float(rules.get("dead_cap_pct", 0.0))
    roster_max   = int(rules.get("roster_max", 14))

    # find this player on team's active roster
    matches = [
        r for r in rosters
        if _get(r, "Team", "team") == team_label
        and (_get(r, "On Roster Flag", "on_roster_flag") or "TRUE").upper() == "TRUE"
        and (_get(r, "On IR?", "on_ir?", "on_ir") or "FALSE").upper() != "TRUE"
        and player_query.lower() in _get(r, " Player Name", "player_name").lower()
    ]
    if not matches:
        return {"status":"INVALID", "reason": f"'{player_query}' not found on {team_label}'s active roster."}

    row = matches[0]
    salary, pname = _salary_for_row(by_pid, by_name, row)
    dead_cap = salary * dead_cap_pct

    before = _active_count(rosters, team_label)
    after  = max(0, before - 1)

    return {
        "status": "VALID",
        "team": team_label,
        "player": pname,
        "salary": round(salary,2),
        "dead_cap": round(dead_cap,2),
        "roster_before": before,
        "roster_after": after,
        "violations": [] if after <= roster_max else [{"code":"ROSTER_MAX", "detail": f"{after} > {roster_max}"}],
        "explain": f"Dropping {pname} creates ${dead_cap:,.0f} dead cap at {dead_cap_pct*100:.0f}%."
    }

def simulate_add(snapshot, team_query: str, player_name: str):
    tabs, by_pid, by_name, rules, rosters = _build_indexes(snapshot)
    team_label = cap_summary(snapshot, team_query)["team_name"]

    roster_max   = int(rules.get("roster_max", 14))
    add_disc_wk  = int(rules.get("add_discount_week", 99))
    curr_week    = int(rules.get("current_week", 1))
    discount_pct = 0.5 if curr_week >= add_disc_wk else 0.0

    base_sal = by_name.get(player_name.lower())
    if base_sal is None:
        return {"status":"INVALID", "reason": f"'{player_name}' not found in Salary2025."}

    eff_sal = base_sal * (1.0 - discount_pct)
    before  = _active_count(rosters, team_label)
    after   = before + 1

    return {
        "status": "VALID",
        "team": team_label,
        "player": player_name,
        "salary_base": round(base_sal,2),
        "salary_effective": round(eff_sal,2),
        "discount_applied": discount_pct,
        "roster_before": before,
        "roster_after": after,
        "violations": [] if after <= roster_max else [{"code":"ROSTER_MAX", "detail": f"{after} > {roster_max}"}],
        "explain": f"Adding {player_name} at ${base_sal:,.0f}" + (f" with {int(discount_pct*100)}% discount → ${eff_sal:,.0f}" if discount_pct>0 else ".")
    }