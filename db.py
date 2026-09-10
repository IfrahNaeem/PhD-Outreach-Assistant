"""
db.py — Persistent data layer for PhD Outreach Assistant, backed by
PostgreSQL (Supabase) via SQLAlchemy.

Every table that holds real data is scoped by user_id, since the app now
supports multiple accounts (see auth.py).

The database connection string is an APP-WIDE secret, configured once by
the developer in Streamlit's Secrets (st.secrets["DATABASE_URL"]) — not
something individual visitors type into the app.
"""

import datetime as dt
import json

import streamlit as st
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, ForeignKey, LargeBinary, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

VALID_MESSAGE_TRANSITIONS = {
    "GENERATED": {"EDITED", "APPROVED", "REJECTED"},
    "EDITED": {"APPROVED", "REJECTED"},
    "APPROVED": {"SENT", "REJECTED"},
    "REJECTED": set(),
    "SENT": set(),
}


# ---------------------------------------------------------------------------
# TABLES
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    your_name = Column(String, default="")
    target_degree = Column(String, default="")
    background = Column(Text, default="")
    email_footer = Column(Text, default="")
    cv_filename = Column(String, nullable=True)
    cv_bytes = Column(LargeBinary, nullable=True)

    # Persisted Gmail OAuth token (google Credentials.to_json()) so a user
    # doesn't have to reconnect Gmail every single session.
    gmail_token_json = Column(Text, nullable=True)


class Professor(Base):
    __tablename__ = "professors"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    professor_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    university = Column(String, default="")
    department = Column(String, default="")
    research_area = Column(String, default="")
    profile_url = Column(String, default="")
    fit_score = Column(Integer, nullable=True)
    score_breakdown_json = Column(Text, nullable=True)
    score_reason = Column(Text, default="")
    research_json = Column(Text, nullable=True)
    source = Column(String, default="MANUAL")
    status = Column(String, default="NEW")
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    professor_id = Column(Integer, ForeignKey("professors.id"), nullable=False, index=True)
    message_type = Column(String, nullable=False)
    subject = Column(String, default="")
    body_html = Column(Text, default="")   # rich content from the editor
    body_text = Column(Text, default="")   # plain-text fallback, auto-derived
    status = Column(String, default="GENERATED")
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    professor_id = Column(Integer, ForeignKey("professors.id"), nullable=False, index=True)
    direction = Column(String, default="INBOUND")
    content = Column(Text, default="")
    classification = Column(String, default="")
    risk_category = Column(String, default="NONE")
    timestamp = Column(DateTime, default=dt.datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String, nullable=False)
    details = Column(Text, default="")
    timestamp = Column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# ENGINE / SESSION
# ---------------------------------------------------------------------------

_engine_cache = {}


def _resolve_connection_string():
    override = st.session_state.get("_db_url_override") if hasattr(st, "session_state") else None
    if override:
        return override
    import os
    env_override = os.environ.get("DATABASE_URL_OVERRIDE")
    if env_override:
        return env_override
    try:
        conn_str = st.secrets.get("DATABASE_URL")
    except Exception:
        conn_str = None
    if not conn_str:
        raise RuntimeError(
            "DATABASE_URL is not configured. Add it under this app's Settings -> Secrets "
            "in Streamlit Cloud (or in .streamlit/secrets.toml when running locally)."
        )
    return conn_str


def _sync_missing_columns(engine):
    """create_all() only creates tables that don't exist yet — it never
    modifies a table that's already there. Since this app doesn't use a
    full migration tool (Alembic), this fills that gap: for any table that
    already exists, check for columns the code now expects but the database
    doesn't have yet, and add them automatically. This means adding a new
    field to a model in this file is enough — no manual SQL required."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # brand new table — create_all() already handled it
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            col_type = col.type.compile(dialect=engine.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'
            with engine.begin() as conn:
                conn.execute(text(ddl))


def _get_engine():
    conn_str = _resolve_connection_string()
    if conn_str not in _engine_cache:
        engine = create_engine(conn_str, pool_pre_ping=True)
        Base.metadata.create_all(engine)
        _sync_missing_columns(engine)
        _engine_cache[conn_str] = engine
    return _engine_cache[conn_str]


def get_session():
    Session = sessionmaker(bind=_get_engine())
    return Session()


# ---------------------------------------------------------------------------
# AUDIT LOG
# ---------------------------------------------------------------------------

def log_audit(user_id, action, details=""):
    s = get_session()
    try:
        s.add(AuditLog(user_id=user_id, action=action, details=details))
        s.commit()
    finally:
        s.close()


def audit_log_dataframe(user_id):
    import pandas as pd
    s = get_session()
    try:
        rows = s.query(AuditLog).filter_by(user_id=user_id).order_by(AuditLog.timestamp.desc()).all()
        if not rows:
            return pd.DataFrame(columns=["Timestamp", "Action", "Details"])
        return pd.DataFrame([{
            "Timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else "",
            "Action": r.action, "Details": r.details
        } for r in rows])
    finally:
        s.close()


# ---------------------------------------------------------------------------
# APPLICANT PROFILE (lives on the User row)
# ---------------------------------------------------------------------------

def save_applicant_profile(user_id, your_name, target_degree, background, email_footer=None):
    s = get_session()
    try:
        u = s.get(User, user_id)
        u.your_name = your_name.strip()
        u.target_degree = target_degree.strip()
        u.background = background.strip()
        if email_footer is not None:
            u.email_footer = email_footer.strip()
        s.commit()
    finally:
        s.close()
    log_audit(user_id, "APPLICANT_PROFILE_SAVED", your_name)


def save_cv(user_id, filename, file_bytes):
    s = get_session()
    try:
        u = s.get(User, user_id)
        u.cv_filename = filename
        u.cv_bytes = file_bytes
        s.commit()
    finally:
        s.close()
    log_audit(user_id, "CV_UPLOADED", filename)


def get_applicant(user_id):
    s = get_session()
    try:
        u = s.get(User, user_id)
        if u is None:
            return {"your_name": "", "target_degree": "", "background": "", "email_footer": "",
                    "cv_filename": None, "cv_bytes": None}
        return {
            "your_name": u.your_name or "",
            "target_degree": u.target_degree or "",
            "background": u.background or "",
            "email_footer": u.email_footer or "",
            "cv_filename": u.cv_filename,
            "cv_bytes": u.cv_bytes,
        }
    finally:
        s.close()


def has_cv(user_id):
    return get_applicant(user_id).get("cv_bytes") is not None


def save_gmail_token(user_id, token_json):
    s = get_session()
    try:
        u = s.get(User, user_id)
        u.gmail_token_json = token_json
        s.commit()
    finally:
        s.close()


def get_gmail_token(user_id):
    s = get_session()
    try:
        u = s.get(User, user_id)
        return u.gmail_token_json if u else None
    finally:
        s.close()


# ---------------------------------------------------------------------------
# PROFESSORS
# ---------------------------------------------------------------------------

def add_professor(user_id, professor_name, email, university, department, research_area,
                   profile_url, source="MANUAL"):
    if not professor_name.strip():
        return False, "Professor name is required."
    if not email.strip():
        return False, "Professor email is required."
    if not research_area.strip():
        return False, "Research area is required."
    s = get_session()
    try:
        prof = Professor(
            user_id=user_id, professor_name=professor_name.strip(), email=email.strip(),
            university=university.strip(), department=department.strip(),
            research_area=research_area.strip(), profile_url=profile_url.strip(),
            source=source, status="NEW",
        )
        s.add(prof)
        s.commit()
        name = prof.professor_name
    finally:
        s.close()
    log_audit(user_id, "PROFESSOR_ADDED", name)
    return True, f"Added: {name}"


def delete_professor(user_id, prof_id):
    """Deletes a professor and everything tied to them (research, messages,
    conversation history) — scoped strictly to the current user."""
    s = get_session()
    try:
        prof = s.query(Professor).filter_by(id=int(prof_id), user_id=user_id).first()
        if prof is None:
            return False, "Professor not found."
        name = prof.professor_name
        s.query(Message).filter_by(user_id=user_id, professor_id=prof_id).delete()
        s.query(Conversation).filter_by(user_id=user_id, professor_id=prof_id).delete()
        s.delete(prof)
        s.commit()
    finally:
        s.close()
    log_audit(user_id, "PROFESSOR_DELETED", name)
    return True, f"Deleted {name} and their message history."


def import_csv(user_id, dataframe):
    cols = {c.lower().strip() for c in dataframe.columns}
    required = {"professor_name", "email", "research_area"}
    missing = required - cols
    if missing:
        return 0, f"CSV must contain columns: {', '.join(sorted(required))}. Missing: {', '.join(sorted(missing))}."
    dataframe = dataframe.rename(columns={c: c.lower().strip() for c in dataframe.columns})
    count = 0
    for _, row in dataframe.iterrows():
        ok, _ = add_professor(
            user_id,
            str(row.get("professor_name", "") or ""), str(row.get("email", "") or ""),
            str(row.get("university", "") or ""), str(row.get("department", "") or ""),
            str(row.get("research_area", "") or ""), str(row.get("profile_url", "") or ""),
            source="CSV_IMPORT",
        )
        if ok:
            count += 1
    return count, f"Imported {count} professor(s)."


def _professor_to_dict(p):
    return {
        "id": p.id, "professor_name": p.professor_name, "email": p.email,
        "university": p.university or "", "department": p.department or "",
        "research_area": p.research_area or "", "profile_url": p.profile_url or "",
        "fit_score": p.fit_score,
        "score_breakdown": json.loads(p.score_breakdown_json) if p.score_breakdown_json else None,
        "score_reason": p.score_reason or "", "source": p.source, "status": p.status,
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else "",
    }


def all_professors(user_id, include_contacted=True):
    s = get_session()
    try:
        rows = s.query(Professor).filter_by(user_id=user_id).order_by(Professor.id).all()
        profs = [_professor_to_dict(p) for p in rows]
    finally:
        s.close()
    if not include_contacted:
        profs = [p for p in profs if p["status"] != "CONTACTED"]
    return profs


def professors_dataframe(user_id, include_contacted=False):
    import pandas as pd
    profs = all_professors(user_id, include_contacted=include_contacted)
    if not profs:
        return pd.DataFrame(columns=["ID", "Professor", "University", "Department", "Fit Score", "Status"])
    return pd.DataFrame([{
        "ID": p["id"], "Professor": p["professor_name"], "University": p["university"],
        "Department": p["department"], "Fit Score": p["fit_score"] if p["fit_score"] is not None else "—",
        "Status": p["status"],
    } for p in profs])


def get_professor(user_id, prof_id):
    s = get_session()
    try:
        p = s.query(Professor).filter_by(id=int(prof_id), user_id=user_id).first()
        return _professor_to_dict(p) if p else None
    finally:
        s.close()


def professor_options(user_id, include_contacted=False):
    profs = all_professors(user_id, include_contacted=include_contacted)
    return {f"#{p['id']} — {p['professor_name']} ({p['university'] or 'no university listed'})": p["id"]
            for p in profs}


def mark_professor_contacted(user_id, prof_id):
    """Called right after an email is actually sent to this professor. Moves
    them out of your active Professors/Messages lists (without deleting
    anything — the sent record stays intact for the dashboard)."""
    s = get_session()
    try:
        p = s.query(Professor).filter_by(id=int(prof_id), user_id=user_id).first()
        if p:
            p.status = "CONTACTED"
            s.commit()
            name = p.professor_name
        else:
            name = None
    finally:
        s.close()
    if name:
        log_audit(user_id, "PROFESSOR_CONTACTED", name)


def is_opted_out(user_id, prof_id):
    p = get_professor(user_id, prof_id)
    return p is not None and p["status"] == "OPTED_OUT"


# ---------------------------------------------------------------------------
# RESEARCH + SCORE
# ---------------------------------------------------------------------------

def save_research(user_id, prof_id, research_data):
    s = get_session()
    try:
        p = s.query(Professor).filter_by(id=int(prof_id), user_id=user_id).first()
        if p:
            p.research_json = json.dumps(research_data)
            p.status = "RESEARCHED"
            s.commit()
    finally:
        s.close()
    log_audit(user_id, "PROFESSOR_RESEARCHED", str(prof_id))


def get_research(user_id, prof_id):
    s = get_session()
    try:
        p = s.query(Professor).filter_by(id=int(prof_id), user_id=user_id).first()
        return json.loads(p.research_json) if p and p.research_json else None
    finally:
        s.close()


def save_score(user_id, prof_id, breakdown, total, reason):
    s = get_session()
    try:
        p = s.query(Professor).filter_by(id=int(prof_id), user_id=user_id).first()
        if p:
            p.fit_score = total
            p.score_breakdown_json = json.dumps(breakdown)
            p.score_reason = reason
            s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# MESSAGES + SAFE MODE APPROVAL WORKFLOW
# ---------------------------------------------------------------------------

def new_message(user_id, prof_id, message_type, subject, body_html, body_text):
    s = get_session()
    try:
        msg = Message(
            user_id=user_id, professor_id=prof_id, message_type=message_type,
            subject=subject, body_html=body_html, body_text=body_text, status="GENERATED",
        )
        s.add(msg)
        s.commit()
        msg_id = msg.id
    finally:
        s.close()
    log_audit(user_id, "MESSAGE_GENERATED", f"{message_type} for professor {prof_id}")
    return msg_id


def _message_to_dict(m):
    return {
        "id": m.id, "prof_id": m.professor_id, "message_type": m.message_type,
        "subject": m.subject or "", "body_html": m.body_html or "", "body_text": m.body_text or "",
        "status": m.status, "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
    }


def all_messages(user_id):
    s = get_session()
    try:
        rows = s.query(Message).filter_by(user_id=user_id).order_by(Message.id).all()
        return [_message_to_dict(m) for m in rows]
    finally:
        s.close()


def get_message(user_id, msg_id):
    s = get_session()
    try:
        m = s.query(Message).filter_by(id=int(msg_id), user_id=user_id).first()
        return _message_to_dict(m) if m else None
    finally:
        s.close()


def messages_for_professor(user_id, prof_id):
    return [m for m in all_messages(user_id) if m["prof_id"] == prof_id]


def messages_dataframe(user_id):
    import pandas as pd
    msgs = all_messages(user_id)
    if not msgs:
        return pd.DataFrame(columns=["ID", "Professor", "Type", "Status", "Preview"])
    rows = []
    for m in msgs:
        p = get_professor(user_id, m["prof_id"])
        preview_source = m["body_text"] or m["body_html"]
        preview = (preview_source[:70] + "…") if len(preview_source) > 70 else preview_source
        rows.append({
            "ID": m["id"], "Professor": p["professor_name"] if p else "?",
            "Type": m["message_type"], "Status": m["status"], "Preview": preview,
        })
    return pd.DataFrame(rows)


def update_message_subject(user_id, msg_id, new_subject):
    s = get_session()
    try:
        m = s.query(Message).filter_by(id=int(msg_id), user_id=user_id).first()
        if m and new_subject.strip() != (m.subject or "").strip():
            m.subject = new_subject
            if m.status == "GENERATED":
                m.status = "EDITED"
            s.commit()
            edited = True
        else:
            edited = False
    finally:
        s.close()
    if edited:
        log_audit(user_id, "MESSAGE_EDITED", f"message {msg_id} (subject)")


def update_message_body(user_id, msg_id, new_html, new_text):
    s = get_session()
    try:
        m = s.query(Message).filter_by(id=int(msg_id), user_id=user_id).first()
        if m and (new_html.strip() != (m.body_html or "").strip()):
            m.body_html = new_html
            m.body_text = new_text
            if m.status == "GENERATED":
                m.status = "EDITED"
            s.commit()
            edited = True
        else:
            edited = False
    finally:
        s.close()
    if edited:
        log_audit(user_id, "MESSAGE_EDITED", f"message {msg_id}")


def transition_message(user_id, msg_id, new_status):
    s = get_session()
    try:
        m = s.query(Message).filter_by(id=int(msg_id), user_id=user_id).first()
        if m is None:
            return False, "Message not found."
        current = m.status
        if new_status == current:
            return False, f"Message is already {current}."
        allowed = VALID_MESSAGE_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            return False, f"Not allowed: cannot move a message from {current} to {new_status}."
        m.status = new_status
        s.commit()
    finally:
        s.close()
    log_audit(user_id, f"MESSAGE_{new_status}", f"message {msg_id}")
    return True, f"Message #{msg_id} moved to {new_status}."


# ---------------------------------------------------------------------------
# CONVERSATIONS / INBOX
# ---------------------------------------------------------------------------

def save_inbound_reply(user_id, prof_id, content, classification, risk_category):
    s = get_session()
    try:
        s.add(Conversation(
            user_id=user_id, professor_id=prof_id, direction="INBOUND", content=content,
            classification=classification, risk_category=risk_category,
        ))
        if classification == "NOT_INTERESTED_NO_FURTHER_CONTACT":
            p = s.query(Professor).filter_by(id=prof_id, user_id=user_id).first()
            if p:
                p.status = "OPTED_OUT"
        s.commit()
    finally:
        s.close()
    if classification == "NOT_INTERESTED_NO_FURTHER_CONTACT":
        log_audit(user_id, "OPT_OUT_RECEIVED", str(prof_id))


def conversations_for_professor(user_id, prof_id):
    s = get_session()
    try:
        rows = s.query(Conversation).filter_by(user_id=user_id, professor_id=prof_id) \
            .order_by(Conversation.timestamp.desc()).all()
        return [{
            "timestamp": c.timestamp.strftime("%Y-%m-%d %H:%M:%S") if c.timestamp else "",
            "classification": c.classification, "content": c.content,
        } for c in rows]
    finally:
        s.close()


def all_conversations(user_id):
    s = get_session()
    try:
        return s.query(Conversation).filter_by(user_id=user_id).count()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

def dashboard_stats(user_id):
    profs = all_professors(user_id)
    msgs = all_messages(user_id)
    return {
        "total_professors": len(profs),
        "strong_fits": len([p for p in profs if (p["fit_score"] or 0) >= 60]),
        "awaiting_approval": len([m for m in msgs if m["status"] in ("GENERATED", "EDITED")]),
        "sent": len([m for m in msgs if m["status"] == "SENT"]),
        "replies": all_conversations(user_id),
        "opted_out": len([p for p in profs if p["status"] == "OPTED_OUT"]),
    }


def dashboard_by_university(user_id):
    """Breaks down professors and email progress by University + Department,
    so you can see at a glance where outreach stands across each school."""
    import pandas as pd
    profs = all_professors(user_id)
    msgs = all_messages(user_id)
    prof_lookup = {p["id"]: p for p in profs}

    groups = {}
    for p in profs:
        key = (p["university"] or "(no university listed)", p["department"] or "(no department listed)")
        g = groups.setdefault(key, {"prof_ids": set(), "generated": 0, "sent": 0})
        g["prof_ids"].add(p["id"])

    for m in msgs:
        if m["message_type"] != "EMAIL":
            continue  # count the main outreach email, not every follow-up, per professor
        p = prof_lookup.get(m["prof_id"])
        if not p:
            continue
        key = (p["university"] or "(no university listed)", p["department"] or "(no department listed)")
        g = groups.setdefault(key, {"prof_ids": set(), "generated": 0, "sent": 0})
        g["generated"] += 1
        if m["status"] == "SENT":
            g["sent"] += 1

    if not groups:
        return pd.DataFrame(columns=["University", "Department", "Professors",
                                      "Emails Generated", "Emails Sent", "Emails Not Sent"])

    rows = []
    for (uni, dept), g in sorted(groups.items()):
        rows.append({
            "University": uni, "Department": dept,
            "Professors": len(g["prof_ids"]),
            "Emails Generated": g["generated"],
            "Emails Sent": g["sent"],
            "Emails Not Sent": g["generated"] - g["sent"],
        })
    return pd.DataFrame(rows)


def sent_emails_dataframe(user_id):
    """Every email that's actually been sent, with the professor's info —
    this is the record of who you've contacted, since they no longer show
    up in your active Professors list once sent."""
    import pandas as pd
    msgs = [m for m in all_messages(user_id) if m["message_type"] == "EMAIL" and m["status"] == "SENT"]
    if not msgs:
        return pd.DataFrame(columns=["Professor", "Email", "University", "Department", "Research Area", "Sent At"])
    rows = []
    for m in msgs:
        p = get_professor(user_id, m["prof_id"])
        if not p:
            continue
        rows.append({
            "Professor": p["professor_name"], "Email": p["email"],
            "University": p["university"], "Department": p["department"],
            "Research Area": p["research_area"], "Sent At": m["created_at"],
        })
    return pd.DataFrame(rows)
