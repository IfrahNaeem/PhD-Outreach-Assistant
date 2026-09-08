import streamlit as st

ACCENT = "#4f46e5"


def inject_minimal_css():
    st.markdown(f"""
    <style>
        .block-container {{ padding-top: 2rem; max-width: 1000px; }}
        h1, h2, h3 {{ font-weight: 600 !important; letter-spacing: -0.01em; }}
        div[data-testid="stMetric"] {{
            background: #fafafa; border: 1px solid #eee; border-radius: 10px; padding: 12px 16px;
        }}
        .po-safe-banner {{
            border: 1px solid #d1fae5; background: #ecfdf5; color: #065f46;
            border-radius: 10px; padding: 10px 14px; font-size: 13.5px; margin-bottom: 1rem;
        }}
        .po-muted {{ color: #6b7280; font-size: 13.5px; }}
        div.stButton > button {{ border-radius: 8px; }}
        div.stButton > button[kind="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; }}
    </style>
    """, unsafe_allow_html=True)


def page_header(title, subtitle=None):
    st.markdown(f"## {title}")
    if subtitle:
        st.markdown(f'<span class="po-muted">{subtitle}</span>', unsafe_allow_html=True)
    st.write("")


def safe_mode_banner():
    st.markdown(
        '<div class="po-safe-banner">🛡️ <b>Safe Mode is on.</b> '
        'No email is ever sent automatically — everything needs your approval.</div>',
        unsafe_allow_html=True,
    )


def render_score_breakdown(breakdown: dict, total: int, reason: str):
    st.markdown(f"#### Fit score: **{total}/100**")
    for label, pair in breakdown.items():
        value, maxv = pair
        st.progress(value / maxv if maxv else 0, text=f"{label} — {value}/{maxv}")
    if reason:
        st.markdown(f'<div class="po-muted">💬 {reason}</div>', unsafe_allow_html=True)


def render_research(data: dict):
    st.markdown(f"**Summary:** {data.get('professor_summary','')}")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**✅ Verified observations**")
        obs = data.get("verified_observations", [])
        st.markdown("\n".join(f"- {o}" for o in obs) if obs else "_none_")
        st.markdown("**🔗 Potential alignment points**")
        ali = data.get("potential_alignment_points", [])
        st.markdown("\n".join(f"- {o}" for o in ali) if ali else "_none_")
    with col2:
        st.markdown("**🤔 Inferences (not confirmed)**")
        inf = data.get("inferences", [])
        st.markdown("\n".join(f"- {i}" for i in inf) if inf else "_none_")
        st.markdown("**🎯 Personalization angles**")
        ang = data.get("personalization_angles", [])
        st.markdown("\n".join(f"- {a}" for a in ang) if ang else "_none_")
    notes = data.get("confidence_notes", [])
    if notes:
        st.markdown("**⚠️ Confidence notes**")
        st.markdown("\n".join(f"- {n}" for n in notes))
