"""
auth.py — Simple email/password accounts for PhD Outreach Assistant.

Passwords are hashed with bcrypt before ever touching the database — the
plain password is never stored anywhere. Login state lives in
st.session_state for the current browser session only.
"""

import bcrypt
import streamlit as st

import db


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def sign_up(email, password, confirm_password):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if password != confirm_password:
        return False, "Passwords don't match."

    s = db.get_session()
    try:
        existing = s.query(db.User).filter_by(email=email).first()
        if existing:
            return False, "An account with that email already exists. Try signing in instead."
        user = db.User(email=email, password_hash=hash_password(password))
        s.add(user)
        s.commit()
        user_id = user.id
    finally:
        s.close()

    db.log_audit(user_id, "ACCOUNT_CREATED", email)
    return True, user_id


def sign_in(email, password):
    email = (email or "").strip().lower()
    s = db.get_session()
    try:
        user = s.query(db.User).filter_by(email=email).first()
        if user is None or not verify_password(password, user.password_hash):
            return False, None
        return True, user.id
    finally:
        s.close()


def log_in_session(user_id, email):
    st.session_state["user_id"] = user_id
    st.session_state["user_email"] = email


def is_logged_in():
    return st.session_state.get("user_id") is not None


def current_user_id():
    return st.session_state.get("user_id")


def current_user_email():
    return st.session_state.get("user_email")


def log_out():
    for key in ("user_id", "user_email", "gmail_creds", "gmail_auth_url", "gmail_pasted_url"):
        st.session_state.pop(key, None)
