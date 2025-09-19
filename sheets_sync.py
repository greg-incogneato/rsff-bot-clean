# sheets_sync.py
import os
import hashlib
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Scope we need: read-only access
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_SERVICE = None  # global singleton client


def _get_service():
    """Return a cached Google Sheets service client."""
    global _SERVICE
    if _SERVICE is None:
        # Use service account JSON (either from file or temp file set in app.py shim)
        creds = service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            scopes=_SCOPES
        )
        _SERVICE = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _SERVICE


def pull_snapshot(sheet_id: str, ranges: list[str]):
    """
    Fetch specified ranges from a Google Sheet and return a structured snapshot dict.
    Example ranges: ["Salary2025!A1:F1000", "Rosters!A1:K1000", "Owners2025!A1:F1000", "Rules!A1:B995"]
    """
    service = _get_service()
    result = service.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id,
        ranges=ranges,
        majorDimension="ROWS"
    ).execute()

    tabs = {}
    for resp in result.get("valueRanges", []):
        rng = resp.get("range", "")
        values = resp.get("values", [])
        if not values:
            continue

        # Extract sheet name before "!"
        tab_name = rng.split("!")[0]
        header, *rows = values
        # Normalize into list of dicts
        dicts = []
        for r in rows:
            d = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            dicts.append(d)
        tabs[tab_name] = dicts

    # Snapshot metadata
    h = hashlib.md5(str(tabs).encode()).hexdigest()[:8]
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {"hash": h, "ts": ts, "tabs": tabs}