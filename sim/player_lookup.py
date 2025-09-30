# sim/player_lookup.py
from __future__ import annotations
from typing import Dict, Any, List, Tuple
import re

def _norm_key(k: Any) -> str:
    # strip, collapse inner spaces, lowercase
    return re.sub(r"\s+", " ", str(k or "").strip()).lower()

def _norm(s: Any) -> str:
    return (str(s or "")).strip()

def _low(s: Any) -> str:
    return _norm(s).lower()

def _num(x) -> float:
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

def _norm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {_norm_key(k): v for k, v in (row or {}).items()}

def _build_name_from_norm_row(nr: Dict[str, Any]) -> str:
    for key in ("player name", "player", "name"):
        if key in nr and _norm(nr[key]): return _norm(nr[key])
    # fallback: first non-empty string field
    for v in nr.values():
        if isinstance(v, str) and _norm(v): return _norm(v)
    return ""

def _fuzzy_best(cands: List[str], query: str) -> Tuple[str, int] | Tuple[None, None]:
    q = _low(query)
    if not q or not cands: return (None, None)
    try:
        from rapidfuzz import process, fuzz
        name, score, _ = process.extractOne(query, cands, scorer=fuzz.WRatio, processor=_low)
        return (name, int(score))
    except Exception:
        # fallback: exact lower, then substring
        exact = [c for c in cands if _low(c) == q]
        if exact: return (exact[0], 100)
        part = [c for c in cands if q in _low(c)]
        return (part[0], 80) if part else (None, None)

def player_lookup(snapshot: Dict[str, Any], name_query: str) -> Dict[str, Any] | None:
    tabs = snapshot.get("tabs", {}) or {}
    rosters = tabs.get("Rosters", []) or []
    salary  = tabs.get("Salary2025", []) or tabs.get("Salary", []) or []

    # --- Build roster index (current, On Roster Flag = TRUE) ---
    roster_by_name: Dict[str, dict] = {}
    for r in rosters:
        nr = _norm_row(r)
        if not _is_true(nr.get("on roster flag", "FALSE")):
            continue
        nm = _build_name_from_norm_row(nr)
        if not nm:
            continue
        roster_by_name[nm] = {
            "team_owner": _norm(nr.get("team")),
            "pos": _norm(nr.get("pos") or nr.get("position")),
            "aav": _num(nr.get("aav") or nr.get("salary")),
            "on_ir": _is_true(nr.get("on ir?") or nr.get("ir")),
            "dp": _is_true(nr.get("dp?") or nr.get("dp")),
            "player_id": _norm(nr.get("player id") or nr.get("player_id") or nr.get("id")),
        }

    # --- Build salary DB index (for FA details like NFL/bye or missing AAV) ---
    sal_by_name: Dict[str, dict] = {}
    for s in salary:
        ns = _norm_row(s)
        nm = _build_name_from_norm_row(ns)
        if not nm:
            continue
        sal_by_name[nm] = {
            "pos": _norm(ns.get("pos") or ns.get("position")),
            "nfl": _norm(ns.get("nfl") or ns.get("team")),
            "aav": _num(ns.get("aav") or ns.get("salary")),
            "bye": _norm(ns.get("bye") or ns.get("bye week")),
            "player_id": _norm(ns.get("player id") or ns.get("player_id") or ns.get("id")), 
        }

    # --- Fuzzy pick the player name across both sources ---
    all_names = sorted(set(roster_by_name.keys()) | set(sal_by_name.keys()))
    picked, score = _fuzzy_best(all_names, name_query)
    if not picked or (score is not None and score < 70):
        return None

    roster = roster_by_name.get(picked)  # prefer roster info if present
    info   = sal_by_name.get(picked, {})

    status = "ROSTERED" if roster else "FA"
    aav = float((roster or {}).get("aav") or info.get("aav") or 0.0)
    pos = (roster or {}).get("pos") or info.get("pos") or ""
    nfl = info.get("nfl") or ""  # NFL team typically only lives in salary sheet
    bye = info.get("bye") or ""

    return {
        "name": picked,
        "pos": pos,
        "nfl": nfl,
        "bye": bye,
        "aav": aav,
        "status": status,
        "rostered_by": (roster or {}).get("team_owner"),
        "on_ir": (roster or {}).get("on_ir", False),
        "dp": (roster or {}).get("dp", False),
        "match_score": score or 0,
        "player_id": (roster or {}).get("player_id") or info.get("player_id"),
    }