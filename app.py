"""
app.py — PhD Outreach Assistant (Streamlit)

Run with: streamlit run app.py

Helps a prospective PhD applicant find and email professors: research each
professor, score how good a fit they look like, generate a personalized
supervision-inquiry email (with your CV attached), and only send after your
explicit approval — same Safe Mode principle as before, just for a
different kind of "lead."
"""

import pandas as pd
import streamlit as st

import db
import ai_engine
import gmail_client
import ui_helpers as ui

st.set_page_config(page_title="PhD Outreach Assistant", page_icon="🎓", layout="centered")
ui.inject_minimal_css()

PAGES = [
    "⚙️ Setup",
    "👨‍🏫 Professors",
    "🔍 Research & Fit",
    "✉️ Messages",
    "📋 Approval Queue",
    "📥 Inbox",
    "📊 Dashboard",
]

with st.sidebar:
    st.markdown("### 🎓 PhD Outreach Assistant")
    st.caption("Find professors. Personalize outreach. Land a supervisor.")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.divider()
    st.caption("🛡️ Safe Mode: nothing sends without your approval.")
    st.caption("🟢 AI connected" if ai_engine.is_connected() else "⚪ AI not connected yet")
    st.caption("🟢 Gmail connected" if gmail_client.is_connected() else "⚪ Gmail not connected yet")


# =============================================================================
# PAGE: SETUP
# =============================================================================
if page == "⚙️ Setup":
    ui.page_header("Setup", "Connect your AI key, your Gmail, and tell the app about you.")

    with st.container(border=True):
        st.markdown("#### 1. Connect your Anthropic API key")
        st.caption("Used only in this browser session. Never written to disk.")
        api_key = st.text_input("Anthropic API key", type="password")
        workspace_id = st.text_input(
            "Workspace ID (only fill in if you get an 'anthropic-workspace-id is required' error)",
            placeholder="Leave blank unless you hit that specific error",
        )
        if st.button("Connect AI", type="primary"):
            ok, msg = ai_engine.set_api_key(api_key, workspace_id)
            (st.success if ok else st.error)(msg)

    with st.container(border=True):
        st.markdown("#### 2. Connect Gmail (for real email sending)")
        st.caption(
            "Optional. Without this, approved emails just change status to SENT without actually "
            "being emailed."
        )
        if gmail_client.is_connected():
            st.success("🟢 Gmail is connected.")
            if st.button("Disconnect Gmail"):
                gmail_client.disconnect()
                st.rerun()
        else:
            st.caption(
                "You'll need a `client_secret.json` from your own Google Cloud project "
                "(OAuth client type: Desktop app, scope: gmail.send)."
            )
            secret_file = st.file_uploader("Upload client_secret.json", type=["json"])
            if secret_file is not None:
                ok, msg = gmail_client.set_client_config(secret_file)
                (st.success if ok else st.error)(msg)

            if gmail_client.has_client_config():
                st.divider()
                st.markdown("**Step 1 — Get your sign-in link**")
                if st.button("Get Sign-in Link", type="primary"):
                    ok, msg, url = gmail_client.start_signin()
                    if ok:
                        st.session_state["gmail_auth_url"] = url
                    else:
                        st.error(msg)

                if st.session_state.get("gmail_auth_url"):
                    st.markdown(f"👉 **[Click here to sign in with Google]({st.session_state['gmail_auth_url']})**")
                    st.caption(
                        "After logging in and clicking Allow, Google sends you to a page that fails "
                        "to load — that's expected. Copy the full URL from your address bar (it will "
                        "contain `code=...`) and paste it below."
                    )
                    st.markdown("**Step 2 — Paste it back here**")
                    pasted = st.text_input("Paste the URL from your address bar", key="gmail_pasted_url")
                    if st.button("Complete Connection", type="primary"):
                        ok, msg = gmail_client.complete_signin(pasted)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.session_state.pop("gmail_auth_url", None)
                            st.rerun()

    with st.container(border=True):
        st.markdown("#### 3. Your profile")
        st.caption("This tells the AI who you are, so emails to professors stay honest and specific.")
        applicant = db.get_applicant()
        c1, c2 = st.columns(2)
        with c1:
            your_name = st.text_input("Your name", value=applicant.get("your_name", ""))
        with c2:
            target_degree = st.text_input("Target degree/program", value=applicant.get("target_degree", ""),
                                           placeholder="e.g. PhD in Computer Science")
        background = st.text_area(
            "Your background / research interests",
            value=applicant.get("background", ""),
            placeholder="e.g. I have a Master's in robotics, worked on reinforcement learning for "
                        "manipulator arms, and I'm interested in sim-to-real transfer.",
            height=120,
        )
        if st.button("Save Profile", type="primary"):
            db.save_applicant_profile(your_name, target_degree, background)
            st.success("Profile saved.")

    with st.container(border=True):
        st.markdown("#### 4. Upload your CV")
        st.caption("This gets attached automatically when you send an approved email via Gmail.")
        if db.has_cv():
            st.success(f"🟢 CV on file: {db.get_applicant()['cv_filename']}")
        cv_file = st.file_uploader("Upload CV (PDF recommended)", type=["pdf", "doc", "docx"])
        if cv_file is not None and st.button("Save CV"):
            db.save_cv(cv_file.name, cv_file.getvalue())
            st.success(f"CV saved: {cv_file.name}")
            st.rerun()


# =============================================================================
# PAGE: PROFESSORS
# =============================================================================
elif page == "👨‍🏫 Professors":
    ui.page_header("Professors", "Add professors manually, or import a CSV list.")

    with st.container(border=True):
        st.markdown("#### Add a professor manually")
        st.caption("Only name and email are required — everything else helps the AI personalize better.")
        c1, c2 = st.columns(2)
        professor_name = c1.text_input("Professor name *")
        email = c2.text_input("Email *")
        c3, c4 = st.columns(2)
        university = c3.text_input("University")
        department = c4.text_input("Department")
        research_area = st.text_input("Research area", placeholder="e.g. computational biology, robotics, NLP")
        profile_url = st.text_input("Profile URL (lab page, Google Scholar, etc.)")
        if st.button("➕ Add Professor", type="primary"):
            ok, msg = db.add_professor(professor_name, email, university, department, research_area, profile_url)
            (st.success if ok else st.error)(msg)

    with st.container(border=True):
        st.markdown("#### Or import from CSV")
        st.caption("Required columns: `professor_name, email`. Optional: `university, department, "
                    "research_area, profile_url`.")
        csv_file = st.file_uploader("Upload CSV", type=["csv"])
        if csv_file is not None and st.button("📥 Import CSV"):
            try:
                df = pd.read_csv(csv_file)
                count, msg = db.import_csv(df)
                st.success(msg) if count else st.error(msg)
            except Exception as e:
                st.error(f"Could not read CSV: {e}")

    st.markdown("#### All professors")
    st.dataframe(db.professors_dataframe(), width='stretch', hide_index=True)


# =============================================================================
# PAGE: RESEARCH & FIT
# =============================================================================
elif page == "🔍 Research & Fit":
    ui.page_header("Research & Fit", "AI researches the professor, then a deterministic formula scores the fit.")

    options = db.professor_options()
    if not options:
        st.info("Add a professor first on the Professors page.")
    else:
        label = st.selectbox("Select a professor", list(options.keys()))
        prof_id = options[label]
        prof = db.get_professor(prof_id)
        applicant = db.get_applicant()

        if not applicant.get("background"):
            st.warning("Fill in your background on the Setup page first — it makes research and scoring much better.")

        if st.button("Run AI Research + Fit Scoring", type="primary"):
            ok, msg, data = ai_engine.run_research(prof, applicant)
            if ok:
                db.save_research(prof_id, data)
                breakdown, total, reason = ai_engine.calculate_score(prof, data, applicant)
                db.save_score(prof_id, breakdown, total, reason)
                st.success(msg)
            else:
                st.error(msg)

        research = db.get_research(prof_id)
        if research:
            st.divider()
            ui.render_research(research)
            st.divider()
            ui.render_score_breakdown(prof["score_breakdown"], prof["fit_score"], prof["score_reason"])


# =============================================================================
# PAGE: MESSAGES
# =============================================================================
elif page == "✉️ Messages":
    ui.page_header("Messages", "Generate drafts. Nothing here sends anything — see Approval Queue for that.")

    options = db.professor_options()
    if not options:
        st.info("Add a professor first on the Professors page.")
    else:
        label = st.selectbox("Select a professor", list(options.keys()))
        prof_id = options[label]
        prof = db.get_professor(prof_id)
        research = db.get_research(prof_id)
        applicant = db.get_applicant()

        if not research:
            st.warning("Run research on this professor first (Research & Fit page) for grounded, personalized drafts.")
        if not db.has_cv():
            st.info("No CV uploaded yet (Setup page) — emails will still generate, but won't have anything to attach when sent.")

        with st.container(border=True):
            st.markdown("#### 📧 Supervision inquiry email")
            if st.button("Generate email draft"):
                ok, msg, data = ai_engine.generate_email(prof, applicant, research)
                if ok:
                    db.new_message(prof_id, "EMAIL", data.get("subject", ""), data.get("body", ""))
                    st.success(msg)
                else:
                    st.error(msg)

        with st.container(border=True):
            st.markdown("#### 🔁 Follow-ups (Day 7 / 14)")
            if st.button("Generate follow-ups"):
                ok, msg, data = ai_engine.generate_followups(prof, applicant, research)
                if ok:
                    db.new_message(prof_id, "FOLLOW_UP_DAY7", "", data.get("day7", ""))
                    db.new_message(prof_id, "FOLLOW_UP_DAY14", "", data.get("day14", ""))
                    st.success(msg)
                else:
                    st.error(msg)

        st.divider()
        st.markdown("#### Drafts for this professor")
        msgs = db.messages_for_professor(prof_id)
        if not msgs:
            st.caption("No drafts yet.")
        for m in msgs:
            with st.expander(f"{m['message_type']} · {m['status']} · #{m['id']}"):
                if m["subject"]:
                    st.text_input("Subject", value=m["subject"], key=f"subj_{m['id']}", disabled=True)
                edited = st.text_area("Body", value=m["body"], key=f"body_{m['id']}")
                if edited != m["body"]:
                    db.update_message_body(m["id"], edited)
                st.caption("Go to Approval Queue to approve, reject, or send.")


# =============================================================================
# PAGE: APPROVAL QUEUE
# =============================================================================
elif page == "📋 Approval Queue":
    ui.page_header("Approval Queue", "Only APPROVED messages can ever be marked SENT — enforced in code.")

    msgs = db.all_messages()
    if not msgs:
        st.info("No messages generated yet. Go to the Messages page.")
    else:
        st.dataframe(db.messages_dataframe(), width='stretch', hide_index=True)
        st.divider()

        pending = [m for m in msgs if m["status"] in ("GENERATED", "EDITED")]
        approved = [m for m in msgs if m["status"] == "APPROVED"]

        if pending:
            st.markdown("#### Awaiting your review")
            for m in pending:
                prof = db.get_professor(m["prof_id"])
                with st.container(border=True):
                    st.markdown(f"**#{m['id']} · {m['message_type']} · {prof['professor_name'] if prof else '?'}**")
                    st.write(m["body"])
                    c1, c2 = st.columns(2)
                    if c1.button("✅ Approve", key=f"appr_{m['id']}", type="primary"):
                        ok, msg = db.transition_message(m["id"], "APPROVED")
                        (st.success if ok else st.error)(msg)
                        st.rerun()
                    if c2.button("❌ Reject", key=f"rej_{m['id']}"):
                        ok, msg = db.transition_message(m["id"], "REJECTED")
                        (st.success if ok else st.error)(msg)
                        st.rerun()

        if approved:
            st.markdown("#### Approved — ready to send")
            applicant = db.get_applicant()
            for m in approved:
                prof = db.get_professor(m["prof_id"])
                with st.container(border=True):
                    st.markdown(f"**#{m['id']} · {m['message_type']} · {prof['professor_name'] if prof else '?'}**")
                    st.write(m["body"])

                    opted_out = prof and db.is_opted_out(prof["id"])
                    if opted_out:
                        st.error("This professor asked not to be contacted further — sending is blocked.")
                    elif gmail_client.is_connected():
                        will_attach = db.has_cv()
                        if will_attach:
                            st.caption(f"📎 Will attach: {applicant['cv_filename']}")
                        else:
                            st.caption("⚠️ No CV uploaded — will send without an attachment.")
                        if st.button("📤 Send via Gmail", key=f"send_{m['id']}", type="primary"):
                            to_email = prof["email"] if prof else ""
                            cv_name = applicant.get("cv_filename") if will_attach else None
                            cv_bytes = applicant.get("cv_bytes") if will_attach else None
                            ok, msg = gmail_client.send_email(to_email, m["subject"], m["body"], cv_name, cv_bytes)
                            if ok:
                                db.transition_message(m["id"], "SENT")
                                st.success(msg)
                            else:
                                st.error(msg)
                            st.rerun()
                    else:
                        st.caption("Gmail isn't connected — this will only update the status, not really send.")
                        if st.button("📤 Mark as Sent (simulated)", key=f"send_{m['id']}"):
                            ok, msg = db.transition_message(m["id"], "SENT")
                            (st.success if ok else st.error)(msg)
                            st.rerun()

        if not pending and not approved:
            st.caption("Nothing pending — everything's been actioned.")


# =============================================================================
# PAGE: INBOX
# =============================================================================
elif page == "📥 Inbox":
    ui.page_header("Inbox", "Paste a professor's reply to see how the AI classifies it.")

    options = db.professor_options()
    if not options:
        st.info("Add a professor first on the Professors page.")
    else:
        label = st.selectbox("Which professor is this reply from?", list(options.keys()))
        prof_id = options[label]

        reply_text = st.text_area("Reply text", placeholder="Paste the email reply here...")
        if st.button("Classify Reply", type="primary"):
            if not reply_text.strip():
                st.warning("Paste some reply text first.")
            else:
                ok, msg, data = ai_engine.classify_reply(reply_text)
                if ok:
                    classification = data.get("classification", "UNKNOWN")
                    risk_flag = data.get("risk_flag", False)
                    risk_category = data.get("risk_category", "NONE") if risk_flag else "NONE"
                    db.save_inbound_reply(prof_id, reply_text, classification, risk_category)

                    if classification == "NOT_INTERESTED_NO_FURTHER_CONTACT":
                        st.error("This professor asked not to be contacted further — marked, no more outreach to them.")
                    elif risk_flag:
                        st.warning(f"⚠️ Flagged ({risk_category}) — worth replying to personally rather than with a template.")
                    else:
                        st.success(f"Classified as {classification}.")

                    c1, c2 = st.columns(2)
                    c1.metric("Classification", classification)
                    c2.metric("Risk", risk_category)
                    if data.get("suggested_reply"):
                        st.markdown("**Suggested reply** (still needs your approval before sending):")
                        st.text_area("Suggested reply", value=data["suggested_reply"], label_visibility="collapsed")
                else:
                    st.error(msg)

        history = db.conversations_for_professor(prof_id)
        if history:
            st.divider()
            st.markdown("#### History for this professor")
            for c in reversed(history):
                st.caption(f"{c['timestamp']} · {c['classification']}")
                st.write(c["content"])


# =============================================================================
# PAGE: DASHBOARD
# =============================================================================
elif page == "📊 Dashboard":
    ui.page_header("Dashboard")

    stats = db.dashboard_stats()
    c1, c2, c3 = st.columns(3)
    c1.metric("Total professors", stats["total_professors"])
    c2.metric("Strong fits (score ≥ 60)", stats["strong_fits"])
    c3.metric("Opted-out", stats["opted_out"])

    c4, c5, c6 = st.columns(3)
    c4.metric("Awaiting approval", stats["awaiting_approval"])
    c5.metric("Emails sent", stats["sent"])
    c6.metric("Replies received", stats["replies"])

    st.divider()
    with st.expander("Audit log"):
        st.dataframe(db.audit_log_dataframe(), width='stretch', hide_index=True)
