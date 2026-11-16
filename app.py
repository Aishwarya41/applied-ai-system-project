"""
VibeFinder Streamlit UI.

    streamlit run app.py

A chat front end over src.agent.run_query(). Every answer shows the picks,
any guardrail warnings, and an expandable trace of each pipeline stage.
"""

import os

import streamlit as st
from dotenv import load_dotenv

from src import llm
from src.agent import Result, run_query

load_dotenv()

EXAMPLES = [
    "chill study music",
    "something mellow for studying on a rainy night, no vocals-heavy pop",
    "high energy workout songs",
    "sad but hyper, energy 1.5",
    "romantic dinner jazz",
    "what is the capital of France",
]

STATUS_LABELS = {
    "ok": ("✅", "Grounded picks"),
    "fallback": ("🛟", "Fell back to the plain scorer"),
    "needs_clarification": ("💬", "Needs more detail"),
    "blocked": ("🚫", "Blocked by guardrails"),
}

st.set_page_config(page_title="VibeFinder", page_icon="🎧", layout="centered")


# ---------- state ----------
if "history" not in st.session_state:
    st.session_state.history = []  # list of Result
if "pending" not in st.session_state:
    st.session_state.pending = None


def queue_example(text: str) -> None:
    st.session_state.pending = text


# ---------- sidebar ----------
with st.sidebar:
    st.header("Settings")
    has_key = bool(os.getenv("GEMINI_API_KEY"))
    use_ai = st.toggle(
        "Use Gemini",
        value=has_key,
        disabled=not has_key,
        help="Needs GEMINI_API_KEY in .env. Off = deterministic offline parser.",
    )
    if not has_key:
        st.caption("No `GEMINI_API_KEY` found, so running offline.")
    else:
        st.caption(f"Model: `{llm.model_name()}`")
    k = st.slider("Number of songs", min_value=1, max_value=10, value=5)
    show_trace = st.toggle("Show pipeline trace", value=True)

    with st.expander("Debug: test the self-check loop"):
        sabotage = st.radio(
            "Corrupt Gemini's answer with a fake song",
            options=[0, 1, 2],
            format_func={0: "Off", 1: "First attempt (retry should fix it)",
                         2: "Both attempts (should fall back)"}.get,
            disabled=not use_ai,
        )

    st.divider()
    st.subheader("Try an example")
    for example in EXAMPLES:
        st.button(example, on_click=queue_example, args=(example,), width="stretch")

    st.divider()
    if st.button("Clear chat", width="stretch"):
        st.session_state.history = []
        st.rerun()


# ---------- rendering ----------
def render_song(rank: int, rec: dict) -> None:
    with st.container(border=True):
        left, right = st.columns([5, 1])
        with left:
            st.markdown(f"**{rank}. {rec['title']}** — {rec['artist']}")
            st.caption(f"{rec['genre']} · {rec['mood']}")
        with right:
            st.metric("match", f"{rec['score']:.0%}", label_visibility="collapsed")
        st.write(rec["explanation"])
        st.caption(
            f"energy {rec['energy']:.2f} · valence {rec['valence']:.2f} · "
            f"acoustic {rec['acousticness']:.2f} · id {rec['id']}"
        )


def render_result(result: Result) -> None:
    icon, label = STATUS_LABELS.get(result.status, ("ℹ️", result.status))
    llm_note = f" · {result.llm_calls} Gemini calls, {result.llm_tokens} tokens" if result.llm_calls else ""
    st.caption(f"{icon} {label} · mode: {result.mode}{llm_note} · request {result.request_id}")

    if result.summary:
        st.markdown(f"_{result.summary}_")

    if result.message:
        if result.status == "blocked":
            st.error(result.message)
        else:
            st.info(result.message)

    for warning in result.warnings:
        st.warning(warning, icon="⚠️")

    if result.profile or result.avoid_genres:
        chips = [f"`{key.replace('target_', '').replace('favorite_', '')}: {value}`"
                 for key, value in result.profile.items()]
        chips += [f"`not {g}`" for g in result.avoid_genres]
        st.markdown("**Understood as:** " + " ".join(chips))

    for rank, rec in enumerate(result.recommendations, start=1):
        render_song(rank, rec)

    if show_trace and result.trace:
        with st.expander("Pipeline trace"):
            st.dataframe(
                [
                    {"stage": s.stage, "ok": "✅" if s.ok else "❌", "ms": round(s.ms, 1), "detail": s.detail}
                    for s in result.trace
                ],
                hide_index=True,
                width="stretch",
            )
            if result.retrieved_ids:
                st.caption(f"Retrieved candidate IDs: {result.retrieved_ids}")


# ---------- main ----------
st.title("🎧 VibeFinder")
st.caption("Describe a vibe, activity, or mood. Picks come only from the 28-song catalog.")

for past in st.session_state.history:
    with st.chat_message("user"):
        st.write(past.query or "(empty)")
    with st.chat_message("assistant"):
        render_result(past)

typed = st.chat_input("e.g. upbeat songs for a road trip, no rock")
query = typed if typed is not None else st.session_state.pending
st.session_state.pending = None

if query is not None:
    with st.chat_message("user"):
        st.write(query or "(empty)")
    with st.chat_message("assistant"):
        with st.spinner("Finding your vibe..."):
            result = run_query(query, k=k, offline=not use_ai, corrupt_attempts=sabotage if use_ai else 0)
        render_result(result)
    st.session_state.history.append(result)
