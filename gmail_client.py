"""
gmail_client.py — Real Gmail sending via OAuth for PhD Outreach Assistant.

Two changes from the single-user version:
1. The Google OAuth client config (client_id/client_secret) is now an
   APP-WIDE secret (st.secrets["GOOGLE_CLIENT_CONFIG"]) — configured once
   by the developer, not uploaded by each visitor.
2. Each user's own Gmail token is persisted in the database (db.py) so they
   don't have to reconnect Gmail every single session — it's loaded
   automatically next time they log in.

Emails are now sent as HTML (so headings/bold/bullets from the rich editor
actually render), with a plain-text fallback, and an optional attachment.
"""

import base64
import json
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import streamlit as st

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _get_client_config():
    try:
        if "GOOGLE_CLIENT_CONFIG" not in st.secrets:
            return None
        raw = st.secrets["GOOGLE_CLIENT_CONFIG"]
    except Exception:
        # st.secrets raises if no secrets.toml exists at all (e.g. running
        # locally without one configured yet) — treat that the same as
        # "not configured" rather than crashing the page.
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def is_configured():
    """True if the developer has set up Google OAuth credentials at all."""
    return _get_client_config() is not None


# ---------------------------------------------------------------------------
# PER-USER CONNECTION STATE
# ---------------------------------------------------------------------------

def _load_creds_from_db(user_id):
    import db
    token_json = db.get_gmail_token(user_id)
    if not token_json:
        return None
    from google.oauth2.credentials import Credentials
    try:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    except Exception:
        return None
    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
            db.save_gmail_token(user_id, creds.to_json())
        except Exception:
            return None
    return creds


def ensure_connected(user_id):
    """Loads a previously-saved Gmail connection for this user into the
    current session, if one exists and we haven't already loaded it."""
    if st.session_state.get("gmail_creds") is not None:
        return
    creds = _load_creds_from_db(user_id)
    if creds:
        st.session_state["gmail_creds"] = creds


def is_connected(user_id=None):
    if user_id is not None:
        ensure_connected(user_id)
    return st.session_state.get("gmail_creds") is not None


def disconnect(user_id):
    import db
    st.session_state["gmail_creds"] = None
    db.save_gmail_token(user_id, None)


# ---------------------------------------------------------------------------
# TWO-STEP SIGN-IN (works even when the app isn't on the same machine as
# your browser — same approach as the single-user version)
# ---------------------------------------------------------------------------

def start_signin():
    import secrets
    from google_auth_oauthlib.flow import InstalledAppFlow

    config = _get_client_config()
    if config is None:
        return False, "Gmail sending isn't set up yet — the app owner needs to add GOOGLE_CLIENT_CONFIG in Secrets.", None

    try:
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        flow.redirect_uri = "http://localhost"
        state = secrets.token_urlsafe(16)
        auth_url, _ = flow.authorization_url(state=state, prompt="consent", access_type="offline")
    except Exception as e:
        return False, f"Could not start sign-in: {e}", None

    st.session_state["gmail_flow"] = flow
    return True, "Sign-in link ready.", auth_url


def complete_signin(user_id, pasted_value):
    import db
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

    creds = flow.credentials
    st.session_state["gmail_creds"] = creds
    st.session_state.pop("gmail_flow", None)
    db.save_gmail_token(user_id, creds.to_json())
    return True, "Gmail connected. Approved emails can now be sent for real."


# ---------------------------------------------------------------------------
# SENDING (HTML + plain-text fallback + optional attachment)
# ---------------------------------------------------------------------------

def html_to_plain_text(html):
    """Very small, dependency-free HTML->text fallback for the
    multipart/alternative plain-text part."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(p|h1|h2|h3|h4|h5|h6|div)>", "\n\n", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"<li>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_raw_message(to_email, subject, html_body, plain_body,
                        attachment_filename=None, attachment_bytes=None):
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_body or html_to_plain_text(html_body), "plain"))
    alt.attach(MIMEText(html_body, "html"))

    if attachment_bytes:
        message = MIMEMultipart("mixed")
        message["to"] = to_email
        message["subject"] = subject
        message.attach(alt)
        part = MIMEApplication(attachment_bytes, Name=attachment_filename)
        part["Content-Disposition"] = f'attachment; filename="{attachment_filename}"'
        message.attach(part)
    else:
        message = alt
        message["to"] = to_email
        message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}


def send_email(to_email, subject, html_body, plain_body=None,
                attachment_filename=None, attachment_bytes=None):
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    creds = st.session_state.get("gmail_creds")
    if creds is None:
        return False, "Gmail isn't connected yet. Go to Setup and connect it first."
    if not to_email or not to_email.strip():
        return False, "This professor has no email on file — can't send."

    try:
        service = build("gmail", "v1", credentials=creds)
        raw_message = _build_raw_message(
            to_email.strip(), subject, html_body, plain_body, attachment_filename, attachment_bytes
        )
        service.users().messages().send(userId="me", body=raw_message).execute()
        note = f" with {attachment_filename} attached" if attachment_bytes else ""
        return True, f"Email sent to {to_email}{note}."
    except HttpError as e:
        return False, f"Gmail API rejected the send: {e}"
    except Exception as e:
        return False, f"Send failed: {e}"
