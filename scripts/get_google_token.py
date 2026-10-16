"""Run ONCE on your laptop to let the tracker read your Gmail alerts, send emails and save files to Drive.

    pip install google-auth-oauthlib
    python scripts/get_google_token.py path/to/client_secret.json

A browser opens; sign in as YOUR Google account (the admin) and allow access.
It prints GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN to paste into
GitHub secrets and Streamlit secrets. Keep them private.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
]

if len(sys.argv) != 2:
    sys.exit(__doc__)
flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open(sys.argv[1]))
client = info.get("installed") or info.get("web")
print("\nPaste these as secrets (GitHub → Settings → Secrets → Actions, and Streamlit → App settings → Secrets):\n")
print(f'GOOGLE_CLIENT_ID = "{client["client_id"]}"')
print(f'GOOGLE_CLIENT_SECRET = "{client["client_secret"]}"')
print(f'GOOGLE_REFRESH_TOKEN = "{creds.refresh_token}"')
