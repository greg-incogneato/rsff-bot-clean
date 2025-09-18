import os, json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SHEET_ID = os.getenv("RSFF_SHEET_ID")
RANGES = os.getenv("RSFF_RANGES", "Rules!A:C").split(",")

creds = Credentials.from_service_account_file(
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

resp = svc.spreadsheets().values().batchGet(
    spreadsheetId=SHEET_ID, ranges=RANGES
).execute()

for vr in resp.get("valueRanges", []):
    print("== Range:", vr["range"])
    rows = vr.get("values", [])
    print(f"Rows: {len(rows)}")
    if rows[:2]:
        print("Head:", rows[:2])