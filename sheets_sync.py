# sheets_sync.py
import os, json, hashlib, datetime, re
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def _now_et():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4))).strftime("%Y-%m-%d %H:%M:%S ET")

def _slug(s: str) -> str:
    # lower, strip, collapse spaces, remove non-word chars
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("#","num").replace("?","").replace("/"," ").replace("\\"," ")
    s = re.sub(r"[^a-z0-9 _-]", "", s)
    return s.replace(" ", "_")

def _records_from_values(values):
    if not values or len(values) < 2: return []
    headers = [_slug(h) for h in values[0]]
    out = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):  # skip empty rows
            continue
        rec = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
        out.append(rec)
    return out

def pull_snapshot(sheet_id: str, ranges: list[str]):
    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
    )
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    resp = svc.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id, ranges=ranges
    ).execute()

    tabs = {}
    for vr in resp.get("valueRanges", []):
        rng = vr.get("range","")
        tab = rng.split("!")[0]
        tabs[tab] = _records_from_values(vr.get("values", []))

    snap_hash = hashlib.sha256(json.dumps(tabs, sort_keys=True).encode()).hexdigest()[:10]
    return {"tabs": tabs, "hash": snap_hash, "ts": _now_et()}