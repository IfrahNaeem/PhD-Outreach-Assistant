"""
gmail_client.py — Real Gmail sending via OAuth, with CV attachment support,
for PhD Outreach Assistant.

Same two-step sign-in approach as before (build the link ourselves, you
paste back the redirect URL) — this works even when the app isn't running
on the same machine as your browser.
"""

import base64
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import streamlit as st

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def set_client_config(uploaded_file):
    try:
        config = json.load(uploaded_file)
    except Exception as e:
        return False, f"Could not read that file as JSON: {e}"
    if "installed" not in config and "web" not in config:
        return False, "That doesn't look like a Google OAuth client_secret.json file."
    st.session_state["gmail_client_config"] = config
    return True, "Google credentials file loaded. Now get your sign-in link."


def has_client_config():
    return "gmail_client_config" in st.session_state


def is_connected():
    return st.session_state.get("gmail_creds") is not None


def disconnect():
    st.session_state["gmail_creds"] = None


def start_signin():
    """Step 1: build the sign-in link, don't block/wait for anything."""
    import secrets
    from google_auth_oauthlib.flow import InstalledAppFlow

    config = st.session_state.get("gmail_client_config")
    if config is None:
        return False, "Upload your client_secret.json file first.", None

    try:
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        flow.redirect_uri = "http://localhost"
        state = secrets.token_urlsafe(16)
        auth_url, _ = flow.authorization_url(state=state, prompt="consent", access_type="offline")
    except Exception as e:
        return False, f"Could not start sign-in: {e}", None

    st.session_state["gmail_flow"] = flow
    return True, "Sign-in link ready.", auth_url


def complete_signin(pasted_value):
    """Step 2: exchange the code you copied back for a real token."""
    flow = st.session_state.get("gmail_flow")
    if flow is None:
        return False, "Start the sign-in process first."
    text = (pasted_value or "").strip()
    if not text:
        return False, "Paste the URL (or the code) you got after logging in."
    try:
        if text.startswith("http"):
            flow.fetch_token(authorization_response=text)
        else:
            flow.fetch_token(code=text)
    except Exception as e:
        return False, f"Could not complete sign-in: {e}"

    st.session_state["gmail_creds"] = flow.credentials
    st.session_state.pop("gmail_flow", None)
    return True, "Gmail connected. Approved emails can now be sent for real."


def _build_raw_message(to_email, subject, body, attachment_filename=None, attachment_bytes=None):
    if attachment_bytes:
        message = MIMEMultipart()
        message["to"] = to_email
        message["subject"] = subject
        message.attach(MIMEText(body))

        part = MIMEApplication(attachment_bytes, Name=attachment_filename)
        part["Content-Disposition"] = f'attachment; filename="{attachment_filename}"'
        message.attach(part)
    else:
        message = MIMEText(body)
        message["to"] = to_email
        message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}


def send_email(to_email, subject, body, attachment_filename=None, attachment_bytes=None):
    """Sends one email through the connected Gmail account, optionally with
    a CV (or any file) attached. Returns (ok, message)."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    creds = st.session_state.get("gmail_creds")
    if creds is None:
        return False, "Gmail isn't connected yet. Go to Setup and connect it first."
    if not to_email or not to_email.strip():
        return False, "This professor has no email on file — can't send."

    try:
        service = build("gmail", "v1", credentials=creds)
        raw_message = _build_raw_message(to_email.strip(), subject, body, attachment_filename, attachment_bytes)
        service.users().messages().send(userId="me", body=raw_message).execute()
        note = f" with {attachment_filename} attached" if attachment_bytes else ""
        return True, f"Email sent to {to_email}{note}."
    except HttpError as e:
        return False, f"Gmail API rejected the send: {e}"
    except Exception as e:
        return False, f"Send failed: {e}"
