from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_service():
    creds = None
    if Path("token.json").exists():
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        Path("token.json").write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


if __name__ == "__main__":
    svc = get_service()
    res = svc.users().messages().list(userId="me", q="is:unread", maxResults=5).execute()
    for m in res.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"]
        ).execute()
        h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
        print(h.get("From"), "|", h.get("Subject"))
    print("Done.")