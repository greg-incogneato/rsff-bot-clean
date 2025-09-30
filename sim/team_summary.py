# sim/team_summary.py

from __future__ import annotations
import re
from typing import Dict, Any, List

# ---------- helpers ----------

def _norm_key(k: Any) -> str:
    s = re.sub(r"\s+", " ", str(k or "").strip())
    return s.lower()

def _num(x) -> float:
    """Coerce '$5,489,636', '5,489,636', 5489636, '' or None -> float."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return 0.0
    if s.startswith("$"):
        s = s[1:]
    s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        s = re.sub(r"[^0-9.\-]", "", s)
        return float(s) if s else 0.0

def _is_true(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().upper()
    return s in {"TRUE", "T", "YES", "Y", "1"}

def _norm_val(s: Any) -> str:
    return (str(s or "")).strip()

def _norm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {_norm_key(k): v for k, v in (row or {}).items()}

# ---------- cap limit helpers ----------

def _cap_limit_from_rules(snapshot: Dict[str, Any]) -> float:
    rules = (snapshot.get("tabs", {}) or {}).get("Rules", []) or []
    for r in rules:
        nr = _norm_row(r)
        k = nr.get("key") or nr.get("rule") or nr.get("name")
        v = nr.get("value") or nr.get("val") or nr.get("amount")
        if k and _norm_key(k) == "cap_limit":
            return _num(v)
    return 0.0

def _cap_limit_from_owners(snapshot: Dict[str, Any], team_name: str) -> float:
    owners = (snapshot.get("tabs", {}) or {}).get("Owners2025", []) or []
    for o in owners:
        no = _norm_row(o)
        if _norm_val(no.get("team_name")).lower() == _norm_val(team_name).lower():
            return _num(no.get("cap_limit"))
    return 0.0

# ---------- main ----------

def team_summary(snapshot: Dict[str, Any], team_name: str) -> Dict[str, Any]:
    """
    Full team summary for a given team name.
    Uses Rules.tab['cap_limit'] first, then Owners2025.cap_limit, then fallback=96M.
    """
    tabs = snapshot.get("tabs", {}) or {}
    rosters: List[Dict[str, Any]] = tabs.get("Rosters", []) or []

    # Cap limit resolution
    cap_limit = _cap_limit_from_rules(snapshot)
    if cap_limit <= 0:
        cap_limit = _cap_limit_from_owners(snapshot, team_name)
    if cap_limit <= 0:
        cap_limit = 96_000_000.0  # confirmed fallback for RSFF

    # Filter to this team’s current roster entries
    team_rows: List[Dict[str, Any]] = []
    for r in rosters:
        nr = _norm_row(r)
        if _norm_val(nr.get("team")).lower() != _norm_val(team_name).lower():
            continue
        if not _is_true(nr.get("on roster flag", "FALSE")):
            continue
        team_rows.append(nr)

    active, ir = [], []
    dp_player = None
    dp_relief = 0.0

    for nr in team_rows:
        name = _norm_val(nr.get("player name") or nr.get("name") or nr.get("player"))
        pos = _norm_val(nr.get("pos") or nr.get("position"))
        salary = _num(nr.get("aav") or nr.get("salary"))
        is_ir = _is_true(nr.get("on ir?") or nr.get("ir"))
        is_dp = _is_true(nr.get("dp?") or nr.get("dp"))

        entry = {
            "name": name,
            "pos": pos,
            "salary": float(salary),
            "dp": bool(is_dp),
            "ir": bool(is_ir),
        }

        if is_ir:
            ir.append(entry)
        else:
            active.append(entry)

        if is_dp:
            dp_player = name
            dp_relief += salary

    ir_relief = sum(p["salary"] for p in ir)
    gross_cap = sum(p["salary"] for p in active) + sum(p["salary"] for p in ir)
    cap_used = gross_cap - dp_relief - ir_relief
    if cap_used < 0:
        cap_used = 0.0
    cap_remaining = cap_limit - cap_used

    return {
        "team_name": team_name,
        "cap_limit": float(cap_limit),
        "gross_cap": float(gross_cap),
        "dp_relief": float(dp_relief),
        "ir_relief": float(ir_relief),
        "cap_used": float(cap_used),
        "cap_remaining": float(cap_remaining),
        "dp_player": dp_player,
        "active": active,
        "ir": ir,
        "players_counted": len(active),
    }