#!/usr/bin/env python3
"""
YouTube OAuth 2.0 Setup Helper for Auto-Clipper-Shorts.
Generates your REFRESH_TOKEN for your new YouTube clipping channel.
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

def main():
    print("=" * 60)
    print("🎬 YouTube OAuth Setup for New Clipping Channel")
    print("=" * 60)
    print("1. Go to https://console.cloud.google.com")
    print("2. Enable 'YouTube Data API v3'")
    print("3. Create OAuth 2.0 Client ID (Desktop Application)")
    print("4. Copy Client ID and Client Secret below.\n")

    client_id = input("Enter your CLIENT_ID: ").strip()
    client_secret = input("Enter your CLIENT_SECRET: ").strip()

    if not client_id or not client_secret:
        print("❌ Error: Both Client ID and Client Secret are required.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"]
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print("\n👉 A browser window will open. Log in and choose your NEW YouTube Channel.")
    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

    print("\n" + "=" * 60)
    print("✅ SUCCESS! Here are your GitHub Secrets:")
    print("=" * 60)
    print(f"CLIENT_ID:     {client_id}")
    print(f"CLIENT_SECRET: {client_secret}")
    print(f"REFRESH_TOKEN: {creds.refresh_token}")
    print("=" * 60)
    print("Add these 3 secrets + GROQ_API_KEY to your GitHub repository secrets.")


if __name__ == "__main__":
    main()
