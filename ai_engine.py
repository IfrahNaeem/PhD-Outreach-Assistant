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
# RESEARCH
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM_PROMPT = """You are a careful academic research assistant helping a prospective PhD
applicant evaluate whether a professor could be a good supervisor. You must be strictly honest about
what is a verified fact vs a guess.

Rules you must always follow:
- Never invent specific facts you cannot support (no fake papers, no fake grants, no fake quotes,
  no fake lab names).
- Anything not directly given to you in the input must go under "inferences", clearly framed as a
  reasonable possibility, NOT a certainty.
- position_likelihood_score (0-20) and research_alignment_score (0-10, a sub-signal of alignment
  beyond the deterministic keyword match) must be conservative and justified by what you were given.
- Respond with ONLY a single valid JSON object. No markdown fences, no preamble, no commentary.
"""

RESEARCH_JSON_SHAPE = """{
  "professor_summary": "string, 2-3 sentences about their apparent research focus, based only on what was given",
  "verified_observations": ["string", "..."],
  "inferences": ["string", "..."],
  "potential_alignment_points": ["string", "... specific ways the applicant's background could connect to this professor's work"],
  "personalization_angles": ["string", "..."],
  "confidence_notes": ["string", "..."],
  "position_likelihood_score": 0,
  "research_alignment_score": 0,
  "why_this_professor": "string, 2-3 sentences, plain language"
}"""


def run_research(professor, applicant):
    user_prompt = f"""
APPLICANT BACKGROUND:
- Target degree: {applicant.get('target_degree','')}
- Background/interests: {applicant.get('background','')}

PROFESSOR INFORMATION (this is ALL the information you have — do not assume more exists):
- Name: {professor['professor_name']}
- University: {professor['university']}
- Department: {professor['department']}
- Stated research area: {professor['research_area']}
- Profile URL: {professor['profile_url']}

Return ONLY a JSON object with exactly this shape:
{RESEARCH_JSON_SHAPE}
"""
    try:
        data = call_claude_json(RESEARCH_SYSTEM_PROMPT, user_prompt)
        return True, "Research complete.", data
    except Exception as e:
        return False, f"Research failed: {e}", None


# ---------------------------------------------------------------------------
# DETERMINISTIC FIT SCORING
# The AI only ever supplies two bounded sub-scores. Everything else is
# plain arithmetic, so the number stays explainable.
# ---------------------------------------------------------------------------

def calculate_score(professor, research_data, applicant):
    # Research Alignment (30) — simple keyword overlap between the
    # professor's stated research area and the applicant's background text.
    research_area = (professor.get("research_area") or "").lower()
    background = (applicant.get("background") or "").lower()
    area_words = set(w.strip(".,") for w in research_area.split() if len(w) > 3)
    bg_words = set(w.strip(".,") for w in background.split() if len(w) > 3)
    overlap = len(area_words & bg_words)
    if research_area and overlap >= 2:
        research_alignment = 30
    elif research_area and overlap >= 1:
        research_alignment = 20
    elif research_area:
        research_alignment = 10
    else:
        research_alignment = 5

    # University/Department Context (15) — just rewards having the info at all,
    # since it lets the email be more specific.
    context_score = 15 if professor.get("university") and professor.get("department") else 8

    # Research Alignment sub-signal from AI (10)
    ai_alignment = max(0, min(10, int(research_data.get("research_alignment_score", 4))))

    # Position Likelihood (20) — from AI, clamped
    position_likelihood = max(0, min(20, int(research_data.get("position_likelihood_score", 8))))

    # Contactability (15) — do we have a real-looking, direct email?
    email = professor.get("email", "")
    contactability = 15 if "@" in email and not email.lower().startswith("info@") else 8

    # Data Quality (10) — profile URL present = easier to verify claims
    data_quality = 10 if professor.get("profile_url") else 4

    total = research_alignment + context_score + ai_alignment + position_likelihood + contactability + data_quality
    total = max(0, min(100, total))

    breakdown = {
        "Research Alignment (keywords)": (research_alignment, 30),
        "University/Dept Context": (context_score, 15),
        "Research Alignment (AI)": (ai_alignment, 10),
        "Position Likelihood": (position_likelihood, 20),
        "Contactability": (contactability, 15),
        "Data Quality": (data_quality, 10),
    }
    reason = research_data.get("why_this_professor", "")
    return breakdown, total, reason


# ---------------------------------------------------------------------------
# OUTREACH EMAIL GENERATION
# ---------------------------------------------------------------------------

MESSAGE_SYSTEM_PROMPT = """You write short, honest, respectful academic outreach emails for a
prospective PhD applicant reaching out to a professor. You may ONLY reference facts given to you in
verified_observations or personalization_angles. Never invent papers, grants, prior conversations,
or claim the applicant has read specific work unless told so. Keep an appropriately formal,
humble, academic tone — not a sales pitch. Respond with ONLY a single valid JSON object, no
markdown fences, no commentary."""


def _professor_context(professor, applicant, research):
    research = research or {}
    return f"""
APPLICANT:
- Name: {applicant.get('your_name','')}
- Target degree: {applicant.get('target_degree','')}
- Background: {applicant.get('background','')}
- A CV is attached to this email separately (mention that it's attached; do not describe its contents in detail).

PROFESSOR:
- Name: {professor['professor_name']}
- University: {professor['university']}
- Department: {professor['department']}

VERIFIED OBSERVATIONS (only source of truth you may reference):
{chr(10).join('- ' + o for o in research.get('verified_observations', [])) or '- none'}

ALLOWED PERSONALIZATION ANGLES:
{chr(10).join('- ' + a for a in research.get('personalization_angles', [])) or '- none'}
"""


def generate_email(professor, applicant, research):
    prompt = _professor_context(professor, applicant, research) + """
Write a PhD supervision inquiry EMAIL to this professor. Structure: respectful greeting using their
name, a specific/personal observation about their research (from verified observations only), a brief
2-3 sentence pitch of the applicant's relevant background, a mention that a CV is attached, and a
polite request (e.g. asking if they're considering PhD students / open to a brief call). Under 200 words.
Return ONLY:
{"subject": "string", "body": "string"}
"""
    try:
        data = call_claude_json(MESSAGE_SYSTEM_PROMPT, prompt)
        return True, "Email draft generated.", data
    except Exception as e:
        return False, f"Generation failed: {e}", None


def generate_followups(professor, applicant, research):
    prompt = _professor_context(professor, applicant, research) + """
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
