# sim/ops.py
# Utilities and simulators for add/drop/what-if without mutating the sheet.

from __future__ import annotations
from typing import Dict, Any, List
import re

# ---------- helpers ----------

def _norm(s): 
    return (str(s or "")).strip()

def _low(s): 
    return _norm(s).lower()

def _num(x) -> float:
    """Coerce $, commas, blanks -> float."""
    if x is None: return 0.0
    if isinstance(x, (int, float)): return float(x)
    s = str(x).strip()
    if not s: return 0.0
    if s.startswith("$"): s = s[1:]
    s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        s = re.sub(r"[^0-9.\-]", "", s)
        return float(s) if s else 0.0

def _is_true(v) -> bool:
    if isinstance(v, bool): return v
    return _norm(v).upper() in {"TRUE", "T", "YES", "Y", "1"}

def _norm_key(k: Any) -> str:
    return re.sub(r"\s+", " ", str(k or "").strip()).lower()

def _norm_row(row: dict) -> dict:
    return {_norm_key(k): v for k, v in (row or {}).items()}

def _active_salaries(snapshot: Dict[str, Any], team_name: str, exclude_names: set[str] | None = None) -> list[float]:
    """Return AAVs for active (On Roster Flag TRUE and not IR) players on team, optionally excluding names."""
    if exclude_names is None:
        exclude_names = set()
    rows = (snapshot.get("tabs", {}) or {}).get("Rosters", []) or []
    out = []
    for r in rows:
        nr = _norm_row(r)
        if _low(_norm(nr.get("team"))) != _low(team_name):
            continue
        if not _is_true(nr.get("on roster flag", "FALSE")):
            continue
        if _is_true(nr.get("on ir?") or nr.get("ir")):
            continue  # IR is not eligible for DP
        nm = _norm(nr.get("player name") or nr.get("player") or nr.get("name"))
        if nm in exclude_names:
            continue
        out.append(float(_num(nr.get("aav") or nr.get("salary"))))
    return out

def _current_dp_salary(snapshot: Dict[str, Any], team_name: str) -> float:
    """Return current DP relief (salary) from the roster; if no explicit DP flag, fall back to max active salary."""
    rows = (snapshot.get("tabs", {}) or {}).get("Rosters", []) or []
    dp_sal = 0.0
    max_active = 0.0
    for r in rows:
        nr = _norm_row(r)
        if _low(_norm(nr.get("team"))) != _low(team_name):
            continue
        if not _is_true(nr.get("on roster flag", "FALSE")):
            continue
        sal = float(_num(nr.get("aav") or nr.get("salary")))
        if not _is_true(nr.get("on ir?") or nr.get("ir")):
            if sal > max_active:
                max_active = sal
        if _is_true(nr.get("dp?") or nr.get("dp")):
            dp_sal = sal
    # If sheet has no explicit DP flag, assume the current DP is the max active salary
    return dp_sal if dp_sal > 0 else max_active

# ---------- rules & indexes ----------

def _rules_dict(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    rules = (snapshot.get("tabs", {}) or {}).get("Rules", []) or []
    out = {}
    for r in rules:
        key = r.get("key") or r.get("rule") or r.get("name")
        val = r.get("value") or r.get("val") or r.get("amount")
        if key:
            out[_low(key)] = val
    return out

def _cap_limit_from_rules(snapshot: Dict[str, Any]) -> float:
    rules = _rules_dict(snapshot)
    cap = _num(rules.get("cap_limit"))
    if cap <= 0:
        cap = 96_000_000.0  # confirmed fallback for RSFF
    return cap

def _roster_rows(snapshot: Dict[str, Any], team_name: str) -> List[Dict[str, Any]]:
    rows = (snapshot.get("tabs", {}) or {}).get("Rosters", []) or []
    out = []
    for r in rows:
        nr = _norm_row(r)
        if _low(_norm(nr.get("team"))) != _low(team_name):
            continue
        if not _is_true(nr.get("on roster flag", "FALSE")):
            continue
        out.append(r)  # keep original row for downstream (we normalize when reading)
    return out

def _all_rostered(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Current market: player_name -> team_owner for all rostered players."""
    rows = (snapshot.get("tabs", {}) or {}).get("Rosters", []) or []
    m = {}
    for r in rows:
        nr = _norm_row(r)
        if not _is_true(nr.get("on roster flag", "FALSE")):
            continue
        nm = _norm(nr.get("player name") or nr.get("player") or nr.get("name"))
        tm = _norm(nr.get("team"))
        if nm:
            m[nm] = tm
    return m

def _salary_index(snapshot: Dict[str, Any]) -> Dict[str, float]:
    """Salary DB for FAs: player_name -> AAV."""
    tabs = snapshot.get("tabs", {}) or {}
    sal = tabs.get("Salary2025", []) or tabs.get("Salary", []) or []
    idx = {}
    for s in sal:
        ns = _norm_row(s)
        nm = _norm(ns.get("player name") or ns.get("player") or ns.get("name"))
        if not nm:
            continue
        aav = _num(ns.get("aav") or ns.get("salary"))
        idx[nm] = aav
    return idx

# ---------- fuzzy-ish name picking ----------

def _pick_name(cands: List[str], query: str) -> str | None:
    """
    1) exact lower match
    2) if query is single token, prefer candidates whose LAST token matches it
    3) contains
    4) startswith
    """
    q = _low(query)
    if not q or not cands:
        return None
    # 1) exact
    for c in cands:
        if _low(c) == q:
            return c
    # 2) last-name preference
    if " " not in q:
        ln = q
        last_eq = [c for c in cands if _low(c).split()[-1] == ln]
        if last_eq:
            return sorted(last_eq, key=lambda x: len(x), reverse=True)[0]
    # 3) contains
    for c in cands:
        if q in _low(c):
            return c
    # 4) startswith
    for c in cands:
        if _low(c).startswith(q):
            return c
    return None

# ---------- simulators ----------

def simulate_add(snapshot: Dict[str, Any], team_name: str, player_query: str) -> Dict[str, Any]:
    """
    Simulate adding a player (no sheet mutation).
    - If player is rostered by another team -> INVALID (hard block).
    - If already on your team -> INVALID.
    - If roster is at/over roster_max -> NOT blocked; return violation ROSTER_MAX.
    - AAV comes from Salary sheet. No add discount logic (no % provided by rules).
    """
    rules = _rules_dict(snapshot)
    roster_max = int(_num(rules.get("roster_max", 14)))

    current = _roster_rows(snapshot, team_name)
    roster_before = len(current)

    rostered_map = _all_rostered(snapshot)
    salary_idx   = _salary_index(snapshot)

    all_names = sorted(set(list(rostered_map.keys()) + list(salary_idx.keys())))
    picked = _pick_name(all_names, player_query)
    if not picked:
        return {"status": "INVALID", "reason": f"No player match for '{player_query}'."}

    # availability
    if picked in rostered_map:
        owner = rostered_map[picked]
        if _low(owner) != _low(team_name):
            return {"status": "INVALID", "reason": f"{picked} is already rostered by {owner}.", "availability": f"ROSTERED by {owner}"}
        else:
            return {"status": "INVALID", "reason": f"{picked} is already on your roster.", "availability": f"ROSTERED by {owner}"}

    # advisory only
    violations = []
    if roster_before >= roster_max:
        violations.append({
            "code": "ROSTER_MAX",
            "detail": f"Roster would be {roster_before + 1}/{roster_max}. You must drop someone to make this legal."
        })

    base = float(salary_idx.get(picked, 0.0))  # effective = base (no discount)

    return {
        "status": "OK",
        "team": team_name,
        "player": picked,
        "availability": "FA",
        "salary_base": base,
        "salary_effective": base,
        "discount_applied": 0.0,
        "roster_before": roster_before,
        "roster_after": roster_before + 1,
        "violations": violations,
    }

def simulate_drop(snapshot: Dict[str, Any], team_name: str, player_query: str) -> Dict[str, Any]:
    """
    Simulate dropping a player (no sheet mutation).
    - Validates player is on caller's current roster.
    - Dead cap = AAV * dead_cap_pct (from Rules).
    - Returns flags was_dp/was_ir so caller can compute cap delta correctly.
    """
    rules = _rules_dict(snapshot)
    dead_cap_pct = _num(rules.get("dead_cap_pct", 0)) / 100.0

    rows = _roster_rows(snapshot, team_name)
    by_name = {}
    for r in rows:
        nr = _norm_row(r)
        nm = _norm(nr.get("player name") or nr.get("player") or nr.get("name"))
        if nm:
            by_name[nm] = r

    names_list = list(by_name.keys())
    picked = _pick_name(names_list, player_query)
    if not picked:
        q = _low(player_query)
        if " " not in q:
            last_eq = [n for n in names_list if _low(n).split()[-1] == q]
            if last_eq:
                picked = sorted(last_eq, key=lambda x: len(x), reverse=True)[0]
    if not picked:
        return {"status": "INVALID", "reason": f"{player_query} is not on your current roster."}

    row = by_name[picked]
    nr  = _norm_row(row)
    base = float(_num(nr.get("aav") or nr.get("salary")))
    was_dp = _is_true(nr.get("dp?") or nr.get("dp"))
    was_ir = _is_true(nr.get("on ir?") or nr.get("ir"))

    roster_before = len(rows)
    dead_cap = base * dead_cap_pct if dead_cap_pct > 0 else 0.0

    return {
        "status": "OK",
        "team": team_name,
        "player": picked,
        "salary_base": base,
        "dead_cap": float(dead_cap),
        "roster_before": roster_before,
        "roster_after": max(roster_before - 1, 0),
        "was_dp": bool(was_dp),
        "was_ir": bool(was_ir),
        "violations": [],
    }

def simulate_whatif(snapshot: Dict[str, Any], team_name: str, add_query: str | None, drop_query: str | None) -> Dict[str, Any]:
    """
    Combined 'what if I add X and/or drop Y' simulation with DP re-selection.
    Delta formula:
      used_delta = + add_salary
                   + dead_cap_drop
                   - base_drop
                   + (was_ir ? +base_drop : 0)
                   - (DP_after - DP_before)
    Where:
      DP_after is recomputed from the hypothetical active roster (post-drop, plus add).
    """
    cap_limit = _cap_limit_from_rules(snapshot)

    add_res  = simulate_add(snapshot, team_name, add_query) if add_query else None
    drop_res = simulate_drop(snapshot, team_name, drop_query) if drop_query else None

    # If either branch is INVALID, surface that immediately for clarity
    for r in (add_res, drop_res):
        if r and r.get("status") == "INVALID":
            return {
                "status": "INVALID",
                "reason": r.get("reason"),
                "cap_limit": float(cap_limit),
                "used_delta": 0.0,
                "add_result": add_res,
                "drop_result": drop_res,
                "violations": (add_res.get("violations", []) if add_res else []) + (drop_res.get("violations", []) if drop_res else []),
            }

    # Current DP before scenario
    dp_before = _current_dp_salary(snapshot, team_name)

    # Hypothetical active salaries AFTER operations:
    #  - remove dropped player if present (exclude by name)
    #  - do NOT include IR players
    exclude = set()
    base_drop = 0.0
    was_ir = False
    if drop_res and drop_res.get("status") == "OK":
        exclude.add(drop_res["player"])
        base_drop = float(drop_res["salary_base"])
        was_ir = bool(drop_res.get("was_ir", False))

    act_sals = _active_salaries(snapshot, team_name, exclude_names=exclude)

    # Add salary (if any) is active by default and eligible for DP
    add_salary = 0.0
    if add_res and add_res.get("status") == "OK":
        add_salary = float(add_res["salary_effective"])
        if add_salary > 0:
            act_sals.append(add_salary)

    # DP after scenario = max active salary remaining (including added)
    dp_after = max(act_sals) if act_sals else 0.0

    # Dead cap from drop (if any)
    dead = float(drop_res["dead_cap"]) if drop_res and drop_res.get("status") == "OK" else 0.0

    # Delta used, per formula
    used_delta  = 0.0
    used_delta += add_salary
    used_delta += dead
    used_delta -= base_drop
    if was_ir:
        used_delta += base_drop  # removing IR reduces IR relief, which increases used by +base
    used_delta -= (dp_after - dp_before)

    # Aggregate violations (e.g., ROSTER_MAX from add)
    violations: List[Dict[str, str]] = []
    if add_res and add_res.get("violations"):
        violations.extend(add_res["violations"])

    return {
        "status": "OK",
        "reason": None,
        "cap_limit": float(cap_limit),
        "used_delta": float(used_delta),
        "add_result": add_res,
        "drop_result": drop_res,
        "violations": violations,
        "dp_before": float(dp_before),
        "dp_after": float(dp_after),
    }