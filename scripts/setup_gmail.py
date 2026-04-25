#!/usr/bin/env python3
"""Gmail OAuth setup script for openEar.

Run this locally on a machine with a web browser to generate the
initial OAuth refresh token for Gmail API access.

Usage:
    python scripts/setup_gmail.py

Prerequisites:
    1. Create a Google Cloud project at console.cloud.google.com
    2. Enable the Gmail API
    3. Create OAuth 2.0 credentials (Desktop application)
    4. Download the credentials JSON file
    5. Set the project to Testing status and add your Google account
       as a test user

The script will:
    1. Open a browser for the OAuth consent flow
    2. Save the resulting token to the configured path
    3. Optionally upload to AWS SSM Parameter Store
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Set up Gmail OAuth for openEar")
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to OAuth client credentials JSON file",
    )
    parser.add_argument(
        "--token-output",
        default="token.json",
        help="Path to save the generated token",
    )
    parser.add_argument(
        "--upload-ssm",
        action="store_true",
        help="Upload token to AWS SSM Parameter Store",
    )
    parser.add_argument(
        "--ssm-region",
        default="us-west-2",
        help="AWS region for SSM (default: us-west-2)",
    )
    args = parser.parse_args()

    if not Path(args.credentials).exists():
        print(f"Error: Credentials file not found: {args.credentials}")
        print(
            "Download it from Google Cloud Console > APIs & Services > Credentials"
        )
        sys.exit(1)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Error: Required packages not installed.")
        print("Run: pip install google-auth-oauthlib google-auth-httplib2")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = None
    token_path = Path(args.token_output)

    # Check for existing token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            print("Starting OAuth flow. A browser window will open...")
            flow = InstalledAppFlow.from_client_secrets_file(
                args.credentials, SCOPES
            )
            creds = flow.run_local_server(port=0)

    # Save token locally
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Token saved to {token_path}")

    # Verify the token works
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        print(f"Authenticated as: {profile.get('emailAddress')}")
    except Exception as e:
        print(f"Warning: Could not verify token: {e}")

    # Optionally upload to SSM
    if args.upload_ssm:
        try:
            import boto3

            ssm = boto3.client("ssm", region_name=args.ssm_region)

            # Store refresh token
            token_data = json.loads(creds.to_json())
            ssm.put_parameter(
                Name="/openear/gmail/refresh_token",
                Value=token_data.get("refresh_token", ""),
                Type="SecureString",
                Overwrite=True,
            )
            print("Refresh token uploaded to SSM: /openear/gmail/refresh_token")

        except Exception as e:
            print(f"Error uploading to SSM: {e}")
            sys.exit(1)

    print("\nSetup complete!")


if __name__ == "__main__":
    main()
