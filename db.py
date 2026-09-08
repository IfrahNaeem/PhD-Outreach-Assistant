"""
db.py — Data layer for PhD Outreach Assistant.

Same in-memory, per-browser-session approach as before (see st.session_state).
The core entity here is a PROFESSOR instead of a company/lead — professor
name and email are the required fields; university/department/research area
are optional context that make the AI's research and email much better, but
you can add a professor with just a name and email.
"""

import datetime as dt
import pandas as pd
import streamlit as st

VALID_MESSAGE_TRANSITIONS = {
    "GENERATED": {"EDITED", "APPROVED", "REJECTED"},
    "EDITED": {"APPROVED", "REJECTED"},
    "APPROVED": {"SENT", "REJECTED"},
    "REJECTED": set(),
    "SENT": set(),
}


def _default_store():
    return {
        "applicant": {
            "your_name": "",
            "target_degree": "",          # e.g. "PhD in Computer Science"
            "background": "",             # your research interests / background summary
            "cv_filename": None,
            "cv_bytes": None,
        },
        "professors": [],
        "research": {},        # professor_id -> research dict
        "messages": [],
        "conversations": [],
        "audit_log": [],
        "next_prof_id": 1,
        "next_msg_id": 1,
    }


def get_store():
    if "po_store" not in st.session_state:
        st.session_state["po_store"] = _default_store()
    return st.session_state["po_store"]


def log_audit(action, details=""):
    get_store()["audit_log"].append({
        "action": action,
        "details": details,
        "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# ---------------------------------------------------------------------------
# APPLICANT PROFILE (includes CV)
# ---------------------------------------------------------------------------

def save_applicant_profile(your_name, target_degree, background):
    store = get_store()
    store["applicant"]["your_name"] = your_name.strip()
    store["applicant"]["target_degree"] = target_degree.strip()
    store["applicant"]["background"] = background.strip()
    log_audit("APPLICANT_PROFILE_SAVED", your_name)


def save_cv(filename, file_bytes):
    store = get_store()
    store["applicant"]["cv_filename"] = filename
    store["applicant"]["cv_bytes"] = file_bytes
    log_audit("CV_UPLOADED", filename)


def get_applicant():
    return get_store()["applicant"]


def has_cv():
    return get_applicant().get("cv_bytes") is not None


# ---------------------------------------------------------------------------
# PROFESSORS
# ---------------------------------------------------------------------------

def add_professor(professor_name, email, university, department, research_area,
                   profile_url, source="MANUAL"):
    if not professor_name.strip():
        return False, "Professor name is required."
    if not email.strip():
        return False, "Professor email is required."
    store = get_store()
    prof = {
        "id": store["next_prof_id"],
        "professor_name": professor_name.strip(),
        "email": email.strip(),
        "university": university.strip(),
        "department": department.strip(),
        "research_area": research_area.strip(),
        "profile_url": profile_url.strip(),
        "fit_score": None,
        "score_breakdown": None,
        "score_reason": "",
        "source": source,
        "status": "NEW",
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    store["professors"].append(prof)
    store["next_prof_id"] += 1
    log_audit("PROFESSOR_ADDED", prof["professor_name"])
    return True, f"Added: {prof['professor_name']}"


def import_csv(dataframe):
    cols = {c.lower().strip() for c in dataframe.columns}
    required = {"professor_name", "email"}
    missing = required - cols
    if missing:
        return 0, f"CSV must contain columns: {', '.join(required)}. Missing: {', '.join(missing)}."
    dataframe = dataframe.rename(columns={c: c.lower().strip() for c in dataframe.columns})
    count = 0
    for _, row in dataframe.iterrows():
        ok, _ = add_professor(
            str(row.get("professor_name", "") or ""),
            str(row.get("email", "") or ""),
            str(row.get("university", "") or ""),
            str(row.get("department", "") or ""),
            str(row.get("research_area", "") or ""),
            str(row.get("profile_url", "") or ""),
            source="CSV_IMPORT",
        )
        if ok:
            count += 1
    return count, f"Imported {count} professor(s)."


def all_professors():
    return get_store()["professors"]


def professors_dataframe():
    profs = all_professors()
    if not profs:
        return pd.DataFrame(columns=["ID", "Professor", "University", "Department", "Fit Score", "Status"])
    return pd.DataFrame([{
        "ID": p["id"],
        "Professor": p["professor_name"],
        "University": p["university"],
        "Department": p["department"],
        "Fit Score": p["fit_score"] if p["fit_score"] is not None else "—",
        "Status": p["status"],
    } for p in profs])


def get_professor(prof_id):
    for p in all_professors():
        if p["id"] == int(prof_id):
            return p
    return None


def professor_options():
    return {f"#{p['id']} — {p['professor_name']} ({p['university'] or 'no university listed'})": p["id"]
            for p in all_professors()}


def is_opted_out(prof_id):
    p = get_professor(prof_id)
    return p is not None and p["status"] == "OPTED_OUT"


# ---------------------------------------------------------------------------
# RESEARCH
# ---------------------------------------------------------------------------

def save_research(prof_id, research_data):
    get_store()["research"][prof_id] = research_data
    p = get_professor(prof_id)
    if p:
        p["status"] = "RESEARCHED"
    log_audit("PROFESSOR_RESEARCHED", str(prof_id))


def get_research(prof_id):
    return get_store()["research"].get(prof_id)


def save_score(prof_id, breakdown, total, reason):
    p = get_professor(prof_id)
    if p:
        p["fit_score"] = total
        p["score_breakdown"] = breakdown
        p["score_reason"] = reason


# ---------------------------------------------------------------------------
# MESSAGES + SAFE MODE APPROVAL WORKFLOW
# ---------------------------------------------------------------------------

def new_message(prof_id, message_type, subject, body):
    store = get_store()
    msg = {
        "id": store["next_msg_id"],
        "prof_id": prof_id,
        "message_type": message_type,
        "subject": subject,
        "body": body,
        "status": "GENERATED",
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    store["messages"].append(msg)
    store["next_msg_id"] += 1
    log_audit("MESSAGE_GENERATED", f"{message_type} for professor {prof_id}")
    return msg


def all_messages():
    return get_store()["messages"]


def get_message(msg_id):
    for m in all_messages():
        if m["id"] == int(msg_id):
            return m
    return None


def messages_for_professor(prof_id):
    return [m for m in all_messages() if m["prof_id"] == prof_id]


def messages_dataframe():
    msgs = all_messages()
    if not msgs:
        return pd.DataFrame(columns=["ID", "Professor", "Type", "Status", "Preview"])
    rows = []
    for m in msgs:
        p = get_professor(m["prof_id"])
        preview = (m["body"][:70] + "…") if len(m["body"]) > 70 else m["body"]
        rows.append({
            "ID": m["id"],
            "Professor": p["professor_name"] if p else "?",
            "Type": m["message_type"],
            "Status": m["status"],
            "Preview": preview,
        })
    return pd.DataFrame(rows)


def update_message_body(msg_id, new_body):
    msg = get_message(msg_id)
    if msg and new_body.strip() != msg["body"].strip():
        msg["body"] = new_body
        if msg["status"] == "GENERATED":
            msg["status"] = "EDITED"
            log_audit("MESSAGE_EDITED", f"message {msg_id}")


def transition_message(msg_id, new_status):
    msg = get_message(msg_id)
    if msg is None:
        return False, "Message not found."
    current = msg["status"]
    if new_status == current:
        return False, f"Message is already {current}."
    allowed = VALID_MESSAGE_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        return False, f"Not allowed: cannot move a message from {current} to {new_status}."
    msg["status"] = new_status
    log_audit(f"MESSAGE_{new_status}", f"message {msg_id}")
    return True, f"Message #{msg_id} moved to {new_status}."


# ---------------------------------------------------------------------------
# CONVERSATIONS / INBOX (professor replies)
# ---------------------------------------------------------------------------

def save_inbound_reply(prof_id, content, classification, risk_category):
    get_store()["conversations"].append({
        "prof_id": prof_id,
        "direction": "INBOUND",
        "content": content,
        "classification": classification,
        "risk_category": risk_category,
        "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    if classification == "NOT_INTERESTED_NO_FURTHER_CONTACT":
        p = get_professor(prof_id)
        if p:
            p["status"] = "OPTED_OUT"
        log_audit("OPT_OUT_RECEIVED", str(prof_id))


def conversations_for_professor(prof_id):
    return [c for c in get_store()["conversations"] if c["prof_id"] == prof_id]


def all_conversations():
    return get_store()["conversations"]


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

def dashboard_stats():
    profs = all_professors()
    msgs = all_messages()
    return {
        "total_professors": len(profs),
        "strong_fits": len([p for p in profs if (p["fit_score"] or 0) >= 60]),
        "awaiting_approval": len([m for m in msgs if m["status"] in ("GENERATED", "EDITED")]),
        "sent": len([m for m in msgs if m["status"] == "SENT"]),
        "replies": len(all_conversations()),
        "opted_out": len([p for p in profs if p["status"] == "OPTED_OUT"]),
    }


def audit_log_dataframe():
    log = get_store()["audit_log"]
    if not log:
        return pd.DataFrame(columns=["Timestamp", "Action", "Details"])
    return pd.DataFrame([{"Timestamp": e["timestamp"], "Action": e["action"], "Details": e["details"]} for e in log])
