#!/usr/bin/env python3
"""Gmail OAuth re-authentication script for headless EC2.

Starts a temporary Flask server with a random URL nonce for security.
The server automatically shuts down after 10 minutes if no auth
completes. Once the OAuth flow succeeds, the new refresh token is
saved to SSM Parameter Store and the Flask server stops.

Usage:
    python scripts/reauth_gmail.py --port 8443

Prerequisites:
    - Security group temporarily allows inbound on the chosen port
    - pip install flask google-auth-oauthlib boto3

C9: This script was missing from the original plan.
Design Review 2 C2: Requires automatic timeout and URL nonce.
"""

import argparse
import json
import logging
import os
import secrets
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Auto-shutdown timeout in seconds (10 minutes)
AUTO_SHUTDOWN_SECONDS = 600


def main():
    parser = argparse.ArgumentParser(
        description="Temporary Flask server for Gmail OAuth re-auth"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8443,
        help="Port to run Flask on (default: 8443)",
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to OAuth client credentials JSON file",
    )
    parser.add_argument(
        "--ssm-region",
        default=os.getenv("AWS_REGION", "us-west-2"),
        help="AWS region for SSM",
    )
    args = parser.parse_args()

    if not Path(args.credentials).exists():
        print(f"Error: Credentials file not found: {args.credentials}")
        sys.exit(1)

    try:
        from flask import Flask, redirect, request, session
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        print("Error: Required packages not installed.")
        print("Run: pip install flask google-auth-oauthlib")
        sys.exit(1)

    # Generate random nonce for URL security
    nonce = secrets.token_urlsafe(16)

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    completed = threading.Event()

    @app.route(f"/reauth/{nonce}")
    def start_auth():
        """Start the OAuth flow."""
        flow = Flow.from_client_secrets_file(
            args.credentials,
            scopes=SCOPES,
            redirect_uri=f"http://localhost:{args.port}/callback/{nonce}",
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        session["state"] = state
        return redirect(auth_url)

    @app.route(f"/callback/{nonce}")
    def callback():
        """Handle the OAuth callback."""
        state = session.get("state")
        flow = Flow.from_client_secrets_file(
            args.credentials,
            scopes=SCOPES,
            state=state,
            redirect_uri=f"http://localhost:{args.port}/callback/{nonce}",
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials

        # Save to SSM
        try:
            import boto3

            ssm = boto3.client("ssm", region_name=args.ssm_region)
            token_data = json.loads(creds.to_json())
            ssm.put_parameter(
                Name="/openear/gmail/refresh_token",
                Value=token_data.get("refresh_token", ""),
                Type="SecureString",
                Overwrite=True,
            )
            logger.info("Refresh token saved to SSM")
        except Exception as e:
            logger.error("Failed to save to SSM: %s", e)
            return f"Error saving token: {e}", 500

        # Also save locally as backup
        token_path = Path("token.json")
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info("Token also saved locally to %s", token_path)

        completed.set()

        return (
            "<h1>Re-authentication successful!</h1>"
            "<p>The new Gmail token has been saved. "
            "You can close this tab. The server will shut down automatically.</p>"
        )

    # Auto-shutdown timer
    def auto_shutdown():
        if not completed.wait(timeout=AUTO_SHUTDOWN_SECONDS):
            logger.warning(
                "Auto-shutdown: no auth completed within %d seconds",
                AUTO_SHUTDOWN_SECONDS,
            )
            os._exit(0)
        else:
            # Give a moment for the response to be sent
            import time

            time.sleep(2)
            logger.info("Auth completed, shutting down Flask server")
            os._exit(0)

    shutdown_thread = threading.Thread(target=auto_shutdown, daemon=True)
    shutdown_thread.start()

    print(f"\n{'=' * 60}")
    print(f"Gmail re-auth server starting on port {args.port}")
    print(f"URL: http://<your-ec2-ip>:{args.port}/reauth/{nonce}")
    print(f"This server will auto-shutdown in {AUTO_SHUTDOWN_SECONDS // 60} minutes.")
    print(f"{'=' * 60}\n")
    print(
        "IMPORTANT: Ensure your security group allows inbound "
        f"TCP on port {args.port} from your IP."
    )
    print()

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
