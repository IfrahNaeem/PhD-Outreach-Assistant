"""
ai_engine.py — AI Provider Interface for PhD Outreach Assistant.

Same architecture as before: every AI call goes through call_claude_json().
Nothing else talks to Anthropic directly.
"""

import json
import re
import streamlit as st


def set_api_key(api_key, workspace_id=""):
    import anthropic
    if not api_key or not api_key.strip():
        st.session_state["po_client"] = None
        return False, "Please paste a valid Anthropic API key."
    extra_headers = {}
    if workspace_id and workspace_id.strip():
        extra_headers["anthropic-workspace-id"] = workspace_id.strip()
    st.session_state["po_client"] = anthropic.Anthropic(
        api_key=api_key.strip(), default_headers=extra_headers or None
    )
    return True, "API key connected. AI features are now active."


def _get_client():
    return st.session_state.get("po_client")


def is_connected():
    return _get_client() is not None


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def call_claude_json(system_prompt, user_prompt, max_tokens=1200):
    client = _get_client()
    if client is None:
        raise RuntimeError("No API key connected yet. Go to Setup and connect your Anthropic API key first.")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    return _extract_json(text)


# ---------------------------------------------------------------------------
# OUTREACH EMAIL GENERATION
# Grounded directly on the professor's basic fields (name, university,
# department, research area) — no separate research step required. Since
# there's less information to work with, there's naturally less room for
# the AI to invent anything beyond what you actually gave it.
# ---------------------------------------------------------------------------

MESSAGE_SYSTEM_PROMPT = """You write short, honest, respectful academic outreach emails for a
prospective PhD applicant reaching out to a professor. You may ONLY reference the professor's name,
university, department, and stated research area given to you below — never invent papers, grants,
prior conversations, awards, or anything else about them. Keep an appropriately formal, humble,
academic tone — not a sales pitch. Respond with ONLY a single valid JSON object, no markdown fences,
no commentary."""


def _professor_context(professor, applicant):
    return f"""
APPLICANT:
- Name: {applicant.get('your_name','')}
- Target degree: {applicant.get('target_degree','')}
- Background: {applicant.get('background','')}
- A CV is attached to this email separately (mention that it's attached; do not describe its contents in detail).

PROFESSOR (this is ALL you know about them — do not assume anything more):
- Name: {professor['professor_name']}
- University: {professor.get('university','')}
- Department: {professor.get('department','')}
- Research area: {professor.get('research_area','')}
"""


def generate_email(professor, applicant):
    prompt = _professor_context(professor, applicant) + """
Write a PhD supervision inquiry EMAIL to this professor. Structure: respectful greeting using their
name, a sentence connecting the applicant's background to the professor's stated research area, a
brief 2-3 sentence pitch of the applicant's relevant background, a mention that a CV is attached, and
a polite request (e.g. asking if they're considering PhD students / open to a brief call). Under 200
words. Return ONLY:
{"subject": "string", "body": "string"}
"""
    try:
        data = call_claude_json(MESSAGE_SYSTEM_PROMPT, prompt)
        return True, "Email draft generated.", data
    except Exception as e:
        return False, f"Generation failed: {e}", None


def generate_followups(professor, applicant):
    prompt = _professor_context(professor, applicant) + """
Write two short, polite follow-up emails: one for 7 days after the first email, one for 14 days after,
assuming no reply yet. Keep them brief, low-pressure, and respectful of the professor's time. Return ONLY:
{"day7": "string", "day14": "string"}
"""
    try:
        data = call_claude_json(MESSAGE_SYSTEM_PROMPT, prompt)
        return True, "Follow-ups generated.", data
    except Exception as e:
        return False, f"Generation failed: {e}", None


# ---------------------------------------------------------------------------
# REPLY CLASSIFICATION
# ---------------------------------------------------------------------------

REPLY_SYSTEM_PROMPT = """You classify inbound email replies from professors responding to a PhD
supervision inquiry. Be conservative about risk flags. Respond with ONLY a single valid JSON object,
no markdown fences, no commentary."""

CLASSIFICATIONS = ["INTERESTED", "REQUESTS_MORE_INFO", "NO_OPENINGS", "NOT_INTERESTED_NO_FURTHER_CONTACT",
                    "OUT_OF_OFFICE", "REDIRECTS_TO_SOMEONE_ELSE", "UNKNOWN"]
RISK_CATEGORIES = ["NEEDS_CAREFUL_PERSONAL_REPLY", "FUNDING_QUESTION", "ADMISSIONS_PROCESS_QUESTION", "NONE"]


def classify_reply(reply_text):
    prompt = f"""
Classify this inbound reply from a professor.

REPLY TEXT:
\"\"\"{reply_text}\"\"\"

Return ONLY:
{{
  "classification": "one of {CLASSIFICATIONS}",
  "risk_flag": true or false,
  "risk_category": "one of {RISK_CATEGORIES}",
  "suggested_reply": "string, a short honest draft reply, or empty string if risk_flag is true"
}}
"""
    try:
        data = call_claude_json(REPLY_SYSTEM_PROMPT, prompt)
        return True, "Classified.", data
    except Exception as e:
        return False, f"Classification failed: {e}", None


# ---------------------------------------------------------------------------
# EXTRACT PROFESSORS FROM A PASTED FACULTY PAGE
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM_PROMPT = """You extract structured directory information from raw text copied from a
university faculty/department webpage. You must be strictly accurate:
- Only include a person if the text clearly identifies them as faculty (professor, lecturer, etc.)
- Only fill in an email if one is LITERALLY present in the text — never guess or construct one
  (e.g., never turn "firstname dot lastname" into a real address).
- Only fill in a research area if the text actually describes one for that person.
- If nothing on the page looks like a faculty directory at all, return an empty list.
- Respond with ONLY a single valid JSON object, no markdown fences, no commentary."""

EXTRACT_JSON_SHAPE = """{
  "professors": [
    {"professor_name": "string", "email": "string or empty", "research_area": "string or empty"}
  ]
}"""


def extract_professors_from_page_text(page_text, university_hint=""):
    prompt = f"""
This text was copied from a faculty/department webpage{f" for {university_hint}" if university_hint else ""}.

PAGE TEXT:
\"\"\"{page_text}\"\"\"

Return ONLY a JSON object with exactly this shape:
{EXTRACT_JSON_SHAPE}
"""
    try:
        data = call_claude_json(EXTRACT_SYSTEM_PROMPT, prompt, max_tokens=2000)
        return True, "Extraction complete.", data.get("professors", [])
    except Exception as e:
        return False, f"Extraction failed: {e}", []
