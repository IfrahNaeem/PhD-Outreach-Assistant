"""
app.py — PhD Outreach Assistant (multi-user, persistent, rich-text version)

Run with: streamlit run app.py

Requires these secrets to be configured (Streamlit Cloud: Settings -> Secrets,
or locally in .streamlit/secrets.toml):

    DATABASE_URL = "postgresql://...supabase connection string..."
    GOOGLE_CLIENT_CONFIG = '''{"installed": {...contents of your client_secret.json...}}'''
"""

import pandas as pd
import streamlit as st
from streamlit_quill import st_quill

import db
import auth
import ai_engine
import gmail_client
import page_fetcher
import ui_helpers as ui

st.set_page_config(page_title="PhD Outreach Assistant", page_icon="🎓", layout="centered")
ui.inject_minimal_css()


# =============================================================================
# LOGIN GATE — nothing below this runs until someone is signed in
# =============================================================================
if not auth.is_logged_in():
    st.markdown("## 🎓 PhD Outreach Assistant")
    st.caption("Find professors. Personalize outreach. Land a supervisor.")
    st.write("")

    tab_signin, tab_signup = st.tabs(["Sign In", "Sign Up"])

    with tab_signin:
        with st.form("signin_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", type="primary")
        if submitted:
            ok, user_id = auth.sign_in(email, password)
            if ok:
                auth.log_in_session(user_id, email.strip().lower())
                st.rerun()
            else:
                st.error("Incorrect email or password.")

    with tab_signup:
        with st.form("signup_form"):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password (min 8 characters)", type="password", key="signup_pw")
            confirm_password = st.text_input("Confirm password", type="password", key="signup_pw2")
            submitted2 = st.form_submit_button("Create Account", type="primary")
        if submitted2:
            ok, result = auth.sign_up(new_email, new_password, confirm_password)
            if ok:
                auth.log_in_session(result, new_email.strip().lower())
                st.success("Account created!")
                st.rerun()
            else:
                st.error(result)

    st.stop()  # nothing past this point renders for a logged-out visitor


# =============================================================================
# LOGGED IN — normal app
# =============================================================================
user_id = auth.current_user_id()

def _footer_html(footer_text):
    """Turns the plain-text footer from the profile into HTML paragraphs,
    with a horizontal rule separating it from the message body."""
    if not footer_text or not footer_text.strip():
        return ""
    lines = footer_text.split("\n")
    return "<hr>" + "".join(f"<p>{line}</p>" if line.strip() else "<br>" for line in lines)


PAGES = [
    "⚙️ Setup",
    "👨‍🏫 Professors",
    "✉️ Messages",
    "📋 Approval Queue",
    "📥 Inbox",
    "📊 Dashboard",
]

with st.sidebar:
    st.markdown("### 🎓 PhD Outreach Assistant")
    st.caption(f"Signed in as {auth.current_user_email()}")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.divider()
    st.caption("🛡️ Safe Mode: nothing sends without your approval.")
    st.caption("🟢 AI connected" if ai_engine.is_connected() else "⚪ AI not connected yet")
    st.caption("🟢 Gmail connected" if gmail_client.is_connected(user_id) else "⚪ Gmail not connected yet")
    st.divider()
    if st.button("Log out"):
        auth.log_out()
        st.rerun()


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
        st.markdown("#### 2. Connect Gmail")
        st.caption("Once connected, it stays connected for your account — no need to redo this every session.")
        if not gmail_client.is_configured():
            st.warning("Gmail sending isn't set up for this app yet — the app owner needs to add "
                       "GOOGLE_CLIENT_CONFIG in Secrets.")
        elif gmail_client.is_connected(user_id):
            st.success("🟢 Gmail is connected.")
            if st.button("Disconnect Gmail"):
                gmail_client.disconnect(user_id)
                st.rerun()
        else:
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
                    ok, msg = gmail_client.complete_signin(user_id, pasted)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.session_state.pop("gmail_auth_url", None)
                        st.rerun()

    with st.container(border=True):
        st.markdown("#### 3. Your profile")
        applicant = db.get_applicant(user_id)
        c1, c2 = st.columns(2)
        with c1:
            your_name = st.text_input("Your name", value=applicant.get("your_name", ""))
        with c2:
            target_degree = st.text_input("Target degree/program", value=applicant.get("target_degree", ""),
                                           placeholder="e.g. PhD in Computer Science")
        background = st.text_area(
            "Your background / research interests", value=applicant.get("background", ""),
            placeholder="e.g. I have a Master's in robotics, worked on reinforcement learning for "
                        "manipulator arms, and I'm interested in sim-to-real transfer.",
            height=120,
        )
        email_footer = st.text_area(
            "Email signature / footer (added automatically to every email you generate)",
            value=applicant.get("email_footer", ""),
            placeholder="e.g.\nBest regards,\nAlex Kim\nM.S. Robotics, University of Example\nlinkedin.com/in/alexkim",
            height=100,
        )
        if st.button("Save Profile", type="primary"):
            db.save_applicant_profile(user_id, your_name, target_degree, background, email_footer)
            st.success("Profile saved.")

    with st.container(border=True):
        st.markdown("#### 4. Upload your CV")
        st.caption("Attached automatically when you send an approved email via Gmail.")
        if db.has_cv(user_id):
            st.success(f"🟢 CV on file: {db.get_applicant(user_id)['cv_filename']}")
        cv_file = st.file_uploader("Upload CV (PDF recommended)", type=["pdf", "doc", "docx"])
        if cv_file is not None and st.button("Save CV"):
            db.save_cv(user_id, cv_file.name, cv_file.getvalue())
            st.success(f"CV saved: {cv_file.name}")
            st.rerun()


# =============================================================================
# PAGE: PROFESSORS
# =============================================================================
elif page == "👨‍🏫 Professors":
    ui.page_header("Professors", "Add professors manually, or import a CSV list.")

    with st.container(border=True):
        st.markdown("#### Add a professor manually")
        st.caption("Name, email, and research area are required. Everything else is optional.")
        c1, c2 = st.columns(2)
        professor_name = c1.text_input("Professor name *")
        email = c2.text_input("Email *")
        c3, c4 = st.columns(2)
        university = c3.text_input("University")
        department = c4.text_input("Department")
        research_area = st.text_input("Research area", placeholder="e.g. computational biology, robotics, NLP")
        profile_url = st.text_input("Profile URL (lab page, Google Scholar, etc.)")
        if st.button("➕ Add Professor", type="primary"):
            ok, msg = db.add_professor(user_id, professor_name, email, university, department,
                                        research_area, profile_url)
            (st.success if ok else st.error)(msg)

    with st.container(border=True):
        st.markdown("#### 🌐 Extract from a faculty page")
        st.caption(
            "Either paste a URL and the app fetches it, or — if that site blocks automated fetching "
            "or needs JavaScript to load — open the page yourself, select-all + copy the visible text, "
            "and paste it directly instead. Either way, the AI only pulls out names/emails/research "
            "areas that are actually present; review the results before adding anyone."
        )
        university_hint = st.text_input("University / department (helps the AI, optional)",
                                          placeholder="e.g. Example University, Computer Science")

        fetch_mode = st.radio("Source", ["Fetch from a URL", "Paste page text myself"],
                               key="extract_source_mode", horizontal=True)

        page_url = ""
        pasted_page_text = ""
        if fetch_mode == "Fetch from a URL":
            page_url = st.text_input("Faculty page URL", placeholder="https://cs.example.edu/people/faculty")
        else:
            pasted_page_text = st.text_area(
                "Paste the page's visible text here",
                placeholder="Open the faculty page in your browser, press Ctrl+A / Cmd+A to select all, "
                            "Ctrl+C / Cmd+C to copy, then paste here.",
                height=200,
            )

        if st.button("Extract Professors"):
            if fetch_mode == "Fetch from a URL":
                ok, msg, text = page_fetcher.fetch_page_text(page_url)
                if not ok:
                    st.error(msg)
                    text = None
            else:
                text = pasted_page_text.strip()
                if not text:
                    st.error("Paste some page text first.")
                    text = None

            if text:
                ok2, msg2, profs = ai_engine.extract_professors_from_page_text(text, university_hint)
                if not ok2:
                    st.error(msg2)
                elif not profs:
                    st.warning("Nothing that looked like a faculty directory was found in that text.")
                else:
                    st.session_state["extracted_professors"] = profs
                    st.success(f"Found {len(profs)} possible professor(s). Review below before adding.")

        extracted = st.session_state.get("extracted_professors")
        if extracted:
            st.markdown("**Review extracted professors** — uncheck any you don't want, edit anything wrong:")
            review_df = pd.DataFrame(extracted)
            if "include" not in review_df.columns:
                review_df.insert(0, "include", True)
            edited_df = st.data_editor(
                review_df, hide_index=True, width='stretch', key="extract_review_editor",
                column_config={"include": st.column_config.CheckboxColumn("Add?")},
            )

            missing_mask = edited_df["research_area"].astype(str).str.strip() == ""
            missing_count = int(missing_mask.sum())
            if missing_count:
                st.caption(f"⚠️ {missing_count} row(s) have no research area (add_professor requires "
                           f"one). You can type them in above, or look them up automatically below.")
                if st.button(f"🔎 Look up missing research areas via web search ({missing_count})"):
                    st.caption("Real web searches, one per person — this may take a little while and "
                               "uses your Anthropic API usage.")
                    rows = edited_df.to_dict("records")
                    missing_idx = [i for i, r in enumerate(rows) if not str(r.get("research_area", "")).strip()]
                    total = len(missing_idx)
                    progress = st.progress(0.0, text="Starting...")
                    found_count = 0
                    for j, i in enumerate(missing_idx):
                        name = rows[i].get("professor_name", "")
                        progress.progress(j / total, text=f"Looking up {name}...")
                        uni = university_hint.split(",")[0].strip() if university_hint else ""
                        dept = university_hint.split(",")[1].strip() if "," in university_hint else ""
                        ok, msg, area = ai_engine.lookup_research_area(name, uni, dept)
                        if ok and area:
                            rows[i]["research_area"] = area
                            found_count += 1
                    progress.progress(1.0, text="Done.")
                    st.success(f"Found research areas for {found_count} of {total} professor(s).")
                    st.session_state["extracted_professors"] = rows
                    st.rerun()

            if st.button("➕ Add Selected to My Professor List", type="primary"):
                added = 0
                skipped = []
                for _, row in edited_df.iterrows():
                    if not row.get("include"):
                        continue
                    ok3, msg3 = db.add_professor(
                        user_id, str(row.get("professor_name", "")), str(row.get("email", "")),
                        university_hint.split(",")[0].strip() if university_hint else "",
                        university_hint.split(",")[1].strip() if "," in university_hint else "",
                        str(row.get("research_area", "")), page_url,
                        source="URL_EXTRACT",
                    )
                    if ok3:
                        added += 1
                    else:
                        skipped.append(f"{row.get('professor_name', '(unnamed)')}: {msg3}")
                st.success(f"Added {added} professor(s) to your list.")
                if skipped:
                    st.warning("Some rows were skipped (usually because no email was found on the "
                               "page — fill it in manually and add them individually above):\n\n" +
                               "\n".join(f"- {s}" for s in skipped))
                else:
                    st.session_state.pop("extracted_professors", None)
                    st.rerun()

    with st.container(border=True):
        st.markdown("#### Or import from CSV")
        st.caption("Required columns: `professor_name, email, research_area`. Optional: `university, "
                    "department, profile_url`.")
        csv_file = st.file_uploader("Upload CSV", type=["csv"])
        if csv_file is not None and st.button("📥 Import CSV"):
            try:
                df = pd.read_csv(csv_file)
                count, msg = db.import_csv(user_id, df)
                st.success(msg) if count else st.error(msg)
            except Exception as e:
                st.error(f"Could not read CSV: {e}")

    st.markdown("#### All professors")
    st.dataframe(db.professors_dataframe(user_id), width='stretch', hide_index=True)

    prof_opts = db.professor_options(user_id)
    if prof_opts:
        st.markdown("#### Delete professors")
        st.caption("Select as many as you want and delete them all at once. Removes their drafts/history too. This can't be undone.")
        del_labels = st.multiselect("Professors to delete", list(prof_opts.keys()), key="delete_prof_select")
        if st.button("🗑️ Delete Selected", disabled=not del_labels):
            deleted = 0
            for lbl in del_labels:
                ok, _ = db.delete_professor(user_id, prof_opts[lbl])
                if ok:
                    deleted += 1
            st.success(f"Deleted {deleted} professor(s).")
            st.rerun()


# =============================================================================

# =============================================================================
# PAGE: MESSAGES — now with a real rich-text editor
# =============================================================================
elif page == "✉️ Messages":
    ui.page_header("Messages", "Generate drafts, then format them however you like before approving.")

    options = db.professor_options(user_id)
    if not options:
        st.info("Add a professor first on the Professors page.")
    else:
        applicant = db.get_applicant(user_id)

        with st.container(border=True):
            st.markdown("#### 🚀 Bulk generate")
            st.caption(
                "Pick several professors and generate a personalized email for each one in a single "
                "click, grounded on their name/university/department/research area. Every email "
                "still needs its own separate approval before it can ever be sent."
            )
            selected_labels = st.multiselect("Select professors", list(options.keys()), key="bulk_select")
            include_followups = st.checkbox("Also generate Day 7 / Day 14 follow-ups for each", key="bulk_followups")

            subject_mode = st.radio(
                "Subject line",
                ["AI-generated per professor", "Use the same subject for all"],
                key="bulk_subject_mode", horizontal=True,
            )
            shared_subject = ""
            if subject_mode == "Use the same subject for all":
                shared_subject = st.text_input("Subject for every email", key="bulk_shared_subject",
                                                placeholder="e.g. PhD Supervision Inquiry")

            if st.button("Generate for Selected", type="primary", disabled=not selected_labels):
                total = len(selected_labels)
                progress = st.progress(0.0, text="Starting...")
                results = []
                footer_html = _footer_html(applicant.get("email_footer", ""))
                footer_text = applicant.get("email_footer", "")

                for i, sel_label in enumerate(selected_labels):
                    pid = options[sel_label]
                    prof = db.get_professor(user_id, pid)
                    progress.progress(i / total, text=f"Generating for {prof['professor_name']}...")

                    ok, msg, data = ai_engine.generate_email(prof, applicant)
                    if not ok:
                        results.append((prof["professor_name"], False, msg))
                        continue

                    body_text = data.get("body", "") + ("\n\n" + footer_text if footer_text else "")
                    body_html = "".join(f"<p>{line}</p>" for line in data.get("body", "").split("\n") if line.strip())
                    body_html += footer_html
                    subject = shared_subject if subject_mode == "Use the same subject for all" else data.get("subject", "")
                    db.new_message(user_id, pid, "EMAIL", subject, body_html, body_text)
                    status_msg = "Email generated."

                    if include_followups:
                        ok2, msg2, data2 = ai_engine.generate_followups(prof, applicant)
                        if ok2:
                            for key, ftype in [("day7", "FOLLOW_UP_DAY7"), ("day14", "FOLLOW_UP_DAY14")]:
                                bt = data2.get(key, "") + ("\n\n" + footer_text if footer_text else "")
                                bh = "".join(f"<p>{line}</p>" for line in data2.get(key, "").split("\n") if line.strip())
                                bh += footer_html
                                db.new_message(user_id, pid, ftype, "", bh, bt)
                            status_msg += " Follow-ups generated too."
                        else:
                            status_msg += f" (Follow-ups failed: {msg2})"

                    results.append((prof["professor_name"], True, status_msg))

                progress.progress(1.0, text="Done.")
                st.markdown("**Results:**")
                for name, ok, msg in results:
                    (st.success if ok else st.warning)(f"{name}: {msg}")

        st.divider()
        st.markdown("#### Review or generate for one professor at a time")
        label = st.selectbox("Select a professor", list(options.keys()))
        prof_id = options[label]
        prof = db.get_professor(user_id, prof_id)

        if not prof.get("research_area"):
            st.caption("💡 No research area on file for this professor — the email will be more generic. "
                       "You can add one on the Professors page.")
        if not db.has_cv(user_id):
            st.info("No CV uploaded yet (Setup page) — emails will still generate, but won't have anything to attach when sent.")

        with st.container(border=True):
            st.markdown("#### 📧 Supervision inquiry email")
            custom_subject = st.text_input("Subject (leave blank to let the AI write one)", key="single_subject")
            if st.button("Generate email draft"):
                ok, msg, data = ai_engine.generate_email(prof, applicant)
                if ok:
                    footer_text = applicant.get("email_footer", "")
                    body_text = data.get("body", "") + ("\n\n" + footer_text if footer_text else "")
                    # Turn the AI's plain-text draft into simple HTML paragraphs
                    # so it opens correctly in the rich editor, then add the footer.
                    body_html = "".join(f"<p>{line}</p>" for line in data.get("body", "").split("\n") if line.strip())
                    body_html += _footer_html(footer_text)
                    subject = custom_subject.strip() if custom_subject.strip() else data.get("subject", "")
                    db.new_message(user_id, prof_id, "EMAIL", subject, body_html, body_text)
                    st.success(msg)
                else:
                    st.error(msg)

        with st.container(border=True):
            st.markdown("#### 🔁 Follow-ups (Day 7 / 14)")
            if st.button("Generate follow-ups"):
                ok, msg, data = ai_engine.generate_followups(prof, applicant)
                if ok:
                    footer_text = applicant.get("email_footer", "")
                    for key, label_ in [("day7", "FOLLOW_UP_DAY7"), ("day14", "FOLLOW_UP_DAY14")]:
                        body_text = data.get(key, "") + ("\n\n" + footer_text if footer_text else "")
                        body_html = "".join(f"<p>{line}</p>" for line in data.get(key, "").split("\n") if line.strip())
                        body_html += _footer_html(footer_text)
                        db.new_message(user_id, prof_id, label_, "", body_html, body_text)
                    st.success(msg)
                else:
                    st.error(msg)

        st.divider()
        st.markdown("#### Drafts for this professor — edit formatting here")
        msgs = db.messages_for_professor(user_id, prof_id)
        if not msgs:
            st.caption("No drafts yet.")
        for m in msgs:
            with st.expander(f"{m['message_type']} · {m['status']} · #{m['id']}"):
                new_subject = st.text_input("Subject", value=m["subject"], key=f"subj_{m['id']}")
                if new_subject != m["subject"]:
                    db.update_message_subject(user_id, m["id"], new_subject)

                st.caption("Use the toolbar for headings, bold, bullets, colors, and font.")
                new_html = st_quill(
                    value=m["body_html"], html=True, key=f"quill_{m['id']}",
                    placeholder="Write your email...",
                )
                if new_html is not None and new_html != m["body_html"]:
                    new_text = gmail_client.html_to_plain_text(new_html)
                    db.update_message_body(user_id, m["id"], new_html, new_text)

                # Re-fetch after any edits above so status/content are current.
                m_now = db.get_message(user_id, m["id"])

                st.divider()
                if m_now["status"] in ("REJECTED", "SENT"):
                    st.caption(f"This message is **{m_now['status']}** — no further action needed here.")
                else:
                    prof_for_send = db.get_professor(user_id, m_now["prof_id"])
                    opted_out = prof_for_send and db.is_opted_out(user_id, prof_for_send["id"])

                    if opted_out:
                        st.error("This professor asked not to be contacted further — sending is blocked.")
                    elif gmail_client.is_connected(user_id):
                        will_attach = db.has_cv(user_id)
                        st.caption(f"📎 Will attach: {applicant['cv_filename']}" if will_attach
                                   else "⚠️ No CV uploaded — will send without an attachment.")
                        c1, c2 = st.columns(2)
                        if c1.button("✅ Approve & Send via Gmail", key=f"quicksend_{m['id']}", type="primary"):
                            if m_now["status"] in ("GENERATED", "EDITED"):
                                db.transition_message(user_id, m_now["id"], "APPROVED")
                            to_email = prof_for_send["email"] if prof_for_send else ""
                            cv_name = applicant.get("cv_filename") if will_attach else None
                            cv_bytes = applicant.get("cv_bytes") if will_attach else None
                            ok, send_msg = gmail_client.send_email(
                                to_email, m_now["subject"], m_now["body_html"], m_now["body_text"], cv_name, cv_bytes
                            )
                            if ok:
                                db.transition_message(user_id, m_now["id"], "SENT")
                                if prof_for_send:
                                    db.mark_professor_contacted(user_id, prof_for_send["id"])
                                st.success(send_msg + " This professor has moved out of your active list.")
                            else:
                                st.error(send_msg)
                            st.rerun()
                        if c2.button("❌ Reject", key=f"quickreject_{m['id']}"):
                            db.transition_message(user_id, m_now["id"], "REJECTED")
                            st.rerun()
                    else:
                        st.caption("Gmail isn't connected (Setup page) — you can still Approve here; "
                                   "actual sending happens once Gmail is connected.")
                        c1, c2 = st.columns(2)
                        if c1.button("✅ Approve", key=f"quickapprove_{m['id']}", type="primary"):
                            if m_now["status"] in ("GENERATED", "EDITED"):
                                db.transition_message(user_id, m_now["id"], "APPROVED")
                                st.rerun()
                        if c2.button("❌ Reject", key=f"quickreject_{m['id']}"):
                            db.transition_message(user_id, m_now["id"], "REJECTED")
                            st.rerun()


# =============================================================================
# PAGE: APPROVAL QUEUE
# =============================================================================
elif page == "📋 Approval Queue":
    ui.page_header("Approval Queue", "Only APPROVED messages can ever be marked SENT — enforced in code.")

    msgs = db.all_messages(user_id)
    if not msgs:
        st.info("No messages generated yet. Go to the Messages page.")
    else:
        st.dataframe(db.messages_dataframe(user_id), width='stretch', hide_index=True)
        st.divider()

        pending = [m for m in msgs if m["status"] in ("GENERATED", "EDITED")]
        approved = [m for m in msgs if m["status"] == "APPROVED"]

        if pending:
            st.markdown("#### Awaiting your review")
            for m in pending:
                prof = db.get_professor(user_id, m["prof_id"])
                with st.container(border=True):
                    st.markdown(f"**#{m['id']} · {m['message_type']} · {prof['professor_name'] if prof else '?'}**")
                    st.markdown(m["body_html"], unsafe_allow_html=True)
                    c1, c2 = st.columns(2)
                    if c1.button("✅ Approve", key=f"appr_{m['id']}", type="primary"):
                        ok, msg = db.transition_message(user_id, m["id"], "APPROVED")
                        (st.success if ok else st.error)(msg)
                        st.rerun()
                    if c2.button("❌ Reject", key=f"rej_{m['id']}"):
                        ok, msg = db.transition_message(user_id, m["id"], "REJECTED")
                        (st.success if ok else st.error)(msg)
                        st.rerun()

        if approved:
            st.markdown("#### Approved — ready to send")
            applicant = db.get_applicant(user_id)
            for m in approved:
                prof = db.get_professor(user_id, m["prof_id"])
                with st.container(border=True):
                    st.markdown(f"**#{m['id']} · {m['message_type']} · {prof['professor_name'] if prof else '?'}**")
                    st.markdown(m["body_html"], unsafe_allow_html=True)

                    opted_out = prof and db.is_opted_out(user_id, prof["id"])
                    if opted_out:
                        st.error("This professor asked not to be contacted further — sending is blocked.")
                    elif gmail_client.is_connected(user_id):
                        will_attach = db.has_cv(user_id)
                        st.caption(f"📎 Will attach: {applicant['cv_filename']}" if will_attach
                                   else "⚠️ No CV uploaded — will send without an attachment.")
                        if st.button("📤 Send via Gmail", key=f"send_{m['id']}", type="primary"):
                            to_email = prof["email"] if prof else ""
                            cv_name = applicant.get("cv_filename") if will_attach else None
                            cv_bytes = applicant.get("cv_bytes") if will_attach else None
                            ok, msg = gmail_client.send_email(
                                to_email, m["subject"], m["body_html"], m["body_text"], cv_name, cv_bytes
                            )
                            if ok:
                                db.transition_message(user_id, m["id"], "SENT")
                                if prof:
                                    db.mark_professor_contacted(user_id, prof["id"])
                                st.success(msg + " This professor has moved out of your active list.")
                            else:
                                st.error(msg)
                            st.rerun()
                    else:
                        st.caption("Gmail isn't connected — this will only update the status, not really send.")
                        if st.button("📤 Mark as Sent (simulated)", key=f"send_{m['id']}"):
                            ok, msg = db.transition_message(user_id, m["id"], "SENT")
                            if ok and prof:
                                db.mark_professor_contacted(user_id, prof["id"])
                            (st.success if ok else st.error)(msg)
                            st.rerun()

                    if prof and st.button("🗑️ Delete this professor", key=f"delprof_{m['id']}"):
                        ok, msg = db.delete_professor(user_id, prof["id"])
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.rerun()

        if not pending and not approved:
            st.caption("Nothing pending — everything's been actioned.")


# =============================================================================
# PAGE: INBOX
# =============================================================================
elif page == "📥 Inbox":
    ui.page_header("Inbox", "Paste a professor's reply to see how the AI classifies it.")

    options = db.professor_options(user_id, include_contacted=True)
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
                    db.save_inbound_reply(user_id, prof_id, reply_text, classification, risk_category)

                    if classification == "NOT_INTERESTED_NO_FURTHER_CONTACT":
                        st.error("Marked — no more outreach to this professor.")
                    elif risk_flag:
                        st.warning(f"⚠️ Flagged ({risk_category}) — worth replying to personally.")
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

        history = db.conversations_for_professor(user_id, prof_id)
        if history:
            st.divider()
            st.markdown("#### History for this professor")
            for c in history:
                st.caption(f"{c['timestamp']} · {c['classification']}")
                st.write(c["content"])


# =============================================================================
# PAGE: DASHBOARD
# =============================================================================
elif page == "📊 Dashboard":
    ui.page_header("Dashboard")

    stats = db.dashboard_stats(user_id)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total professors", stats["total_professors"])
    c2.metric("Strong fits (score ≥ 60)", stats["strong_fits"])
    c3.metric("Opted-out", stats["opted_out"])

    c4, c5, c6 = st.columns(3)
    c4.metric("Awaiting approval", stats["awaiting_approval"])
    c5.metric("Emails sent", stats["sent"])
    c6.metric("Replies received", stats["replies"])

    st.divider()
    st.markdown("#### By university & department")
    st.caption("Where your outreach stands, broken down by school.")
    st.dataframe(db.dashboard_by_university(user_id), width='stretch', hide_index=True)

    st.divider()
    st.markdown("#### 📧 Sent emails")
    st.caption("Everyone you've actually emailed — they no longer appear in your active Professors list.")
    st.dataframe(db.sent_emails_dataframe(user_id), width='stretch', hide_index=True)

    st.divider()
    with st.expander("Audit log"):
        st.dataframe(db.audit_log_dataframe(user_id), width='stretch', hide_index=True)
