from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from components.cards import (
    _action_step,
    _as_list,
    _doc_card,
    _format_datetime,
    _icon_img,
    _status_badge,
)


def render_review_info_card(case: pd.Series) -> None:
    st.html(
        f"""
        <div class="ad-card ad-panel">
          <h3>Review Info</h3>
          <div class="ad-meta-label">\uac80\ud1a0 \ub2f4\ub2f9\uc790</div>
          <div class="ad-meta-value" style="margin-bottom:20px">{escape(str(case["reviewer"]))}</div>
          <div class="ad-meta-label">\uc870\uce58 \ub2f4\ub2f9\uc790</div>
          <div class="ad-meta-value" style="margin-bottom:22px">{escape(str(case["action_owner"]))}</div>
          <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap">
            {_status_badge(str(case["review_status"]))}
            <span class="ad-row-kicker">Detected At: {escape(_format_datetime(case["detected_at"]))}</span>
          </div>
          <div class="ad-review-actions">
            <div class="ad-button ad-button-primary">Create Review Request</div>
            <div class="ad-button">Mark as In Progress</div>
            <div class="ad-action-row">
              <div class="ad-button">Mark as Resolved</div>
              <div class="ad-button">Mark as False Positive</div>
            </div>
          </div>
        </div>
        """
    )


def render_recommendation_panel(case: pd.Series) -> None:
    docs = _as_list(case["reference_docs"])
    plan = _as_list(case["action_plan"])
    st.html(
        f"""
        <div class="ad-card ad-panel">
          <div class="ad-card ad-panel" style="background:#f8f9fb; box-shadow:none">
            <h3>AI Summary</h3>
            <p>{escape(str(case["ai_summary"]))}</p>
          </div>
          <h3 style="margin-top:28px">\ucc38\uc870 \ubb38\uc11c</h3>
          <div class="ad-doc-grid">
            {''.join(_doc_card(doc, index) for index, doc in enumerate(docs))}
          </div>
          <h3>\uc2e4\ud589 \ub2e8\uacc4 <span style="color:#aeb5bf; font-size:16px">\u24d8</span></h3>
          <div>
            {''.join(_action_step(step, index + 1) for index, step in enumerate(plan))}
          </div>
        </div>
        """
    )


@st.fragment
def render_ai_agent_chat(case: pd.Series) -> None:
    """AI Agent panel that chats over a Risk Card context using RAG."""
    from src import rag_agent

    case_id = str(case.get("case_id", "case"))
    state_key = f"agent_chat_{case_id}"
    pending_key = f"agent_pending_{case_id}"
    scroll_key = f"agent_scroll_bottom_{case_id}"
    if state_key not in st.session_state:
        seed = _as_list(case["ai_agent_messages"])
        greeting = seed[0] if seed else "I've drafted a recommended action. Ask me anything about this case."
        st.session_state[state_key] = [{"role": "assistant", "content": greeting, "citations": []}]
    history = st.session_state[state_key]
    pending_question = st.session_state.get(pending_key)

    bubbles = [_chat_bubble(turn) for turn in history]
    if pending_question:
        bubbles.append(_loading_bubble())

    scroll_id = _safe_dom_id(f"ad-agent-scroll-{case_id}")
    bubbles_html = ''.join(bubbles)
    st.html(
        f"""
        <div class="ad-card ad-panel" style="margin-bottom:0">
          <div class="ad-agent-header">{_icon_img("icon-azure-openai.svg", "AI Agent", "ad-agent-icon")} AI Agent</div>
          <div id="{scroll_id}" class="ad-agent-body">
            {bubbles_html}
          </div>
        </div>
        """
    )
    if st.session_state.pop(scroll_key, False):
        _scroll_agent_to_bottom(scroll_id)

    with st.form(key=f"agent_form_{case_id}", clear_on_submit=True, border=False):
        input_col, send_col = st.columns([4, 1], gap="small", vertical_alignment="bottom")
        with input_col:
            question = st.text_input(
                "Ask the AI Agent a question",
                key=f"agent_input_{case_id}",
                placeholder="Ask the AI Agent a question",
                label_visibility="collapsed",
                disabled=bool(pending_question),
            )
        with send_col:
            submitted = st.form_submit_button("Send", use_container_width=True, disabled=bool(pending_question))

    if submitted and question.strip():
        cleaned_question = question.strip()
        history.append({"role": "user", "content": cleaned_question, "citations": []})
        st.session_state[state_key] = history
        st.session_state[pending_key] = cleaned_question
        st.session_state[scroll_key] = True
        _rerun_agent_fragment()

    if pending_question:
        result = rag_agent.answer_question(case, str(pending_question), history)
        history.append({"role": "assistant", "content": result.text, "citations": result.citations})
        st.session_state[state_key] = history
        st.session_state.pop(pending_key, None)
        _rerun_agent_fragment()

def _rerun_agent_fragment() -> None:
    try:
        st.rerun(scope="fragment")
    except Exception:  # noqa: BLE001
        st.rerun()


def _safe_dom_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)


def _scroll_agent_to_bottom(scroll_id: str) -> None:
    components.html(
        f"""
        <script>
        const target = window.parent.document.getElementById({scroll_id!r});
        if (target) {{ target.scrollTop = target.scrollHeight; }}
        </script>
        """,
        height=0,
        width=0,
    )



def _citation_note(citations: list) -> str:
    if not citations:
        return ""
    items = ", ".join(escape(str(c.get("title") or c.get("chunk_id", ""))) for c in citations)
    return (
        f'<div style="margin-top:8px; font-size:11px; color:#6B7280">\uadfc\uac70: {items}</div>'
    )



def _chat_bubble(turn: dict) -> str:
    content = escape(str(turn.get("content", ""))).replace("\n", "<br>")
    if turn.get("role") == "user":
        return f'<div class="ad-chat-bubble ad-chat-user">{content}</div>'
    citation_html = _citation_note(turn.get("citations", []))
    return f'<div class="ad-chat-bubble"><div class="ad-chat-name">AI Agent</div>{content}{citation_html}</div>'


def _loading_bubble() -> str:
    return (
        '<div class="ad-chat-bubble ad-chat-loading">'
        '<div class="ad-chat-name">AI Agent</div>'
        '<span class="ad-loading-dot"></span>'
        '<span class="ad-loading-dot"></span>'
        '<span class="ad-loading-dot"></span>'
        '<span style="margin-left:8px">Searching the evidence documents and generating an answer.</span>'
        '</div>'
    )

def render_ai_agent_placeholder(case: pd.Series) -> None:
    # \uc774\uc804 \uc815\uc801 placeholder \ud638\ud658\uc6a9 \u2014 \uc2e4\uc81c \ud654\uba74\uc740 render_ai_agent_chat\uc744 \uc0ac\uc6a9\ud55c\ub2e4.
    render_ai_agent_chat(case)
