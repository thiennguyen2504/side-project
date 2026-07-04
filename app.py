"""
app.py — Streamlit web UI for OptiBot.

Features:
  - Single text input for user questions.
  - Displays bot answer and extracted source URLs (Article URL citations).
  - Loads the Gemini File Search Store on startup.
  - Shows clear error messages if the API key is missing.
"""

import os
import re
import logging
import streamlit as st

import uploader

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="OptiBot — OptiSigns Support",
    page_icon="🤖",
    layout="centered",
)

logging.basicConfig(level=logging.INFO)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    body { font-family: 'Inter', sans-serif; }
    .source-box {
        background: #f0f4ff;
        border-left: 4px solid #4f46e5;
        padding: 0.6rem 1rem;
        border-radius: 6px;
        margin-top: 1rem;
    }
    .source-box a { color: #4f46e5; text-decoration: none; }
    .source-box a:hover { text-decoration: underline; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("OptiBot")
st.caption("Customer support assistant powered by OptiSigns documentation.")
st.divider()


def extract_sources(text: str) -> list[str]:
    """
    Extract all Article URLs from the bot's response text.

    Looks for URLs beginning with https://support.optisigns.com/
    or lines starting with 'Article URL:'.

    Returns:
        Deduplicated list of source URL strings.
    """
    # Match explicit citation lines
    sources = re.findall(
        r"https?://support\.optisigns\.com/hc/[^\s\)\]\>\"']+",
        text,
    )
    return list(dict.fromkeys(sources))  # deduplicate, preserve order


# ── Session state: load client & store once ────────────────────────────────────

@st.cache_resource(show_spinner="Connecting to OptiBot knowledge base…")
def load_bot() -> tuple:
    """
    Initialise the Gemini client and retrieve the File Search Store.
    Cached across Streamlit reruns via @st.cache_resource.

    Returns:
        (client, store_name) tuple.
    """
    client = uploader.get_client()
    store = uploader.get_or_create_store(client)
    return client, store.name


# ── Main UI ────────────────────────────────────────────────────────────────────

api_key = os.environ.get("GEMINI_API_KEY", "")

if not api_key:
    st.error(
        "⚠️ **GEMINI_API_KEY** environment variable is not set.  \n"
        "Please set it before starting the app:\n"
        "```bash\nexport GEMINI_API_KEY=your_key_here\n```"
    )
    st.stop()

try:
    client, store_name = load_bot()
except Exception as exc:
    st.error(f"Failed to connect to Gemini: {exc}")
    st.stop()

# ── Chat history ───────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

# Render previous messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.container():
                st.markdown(
                    "<div class='source-box'><strong>📚 Sources:</strong><br>"
                    + "<br>".join(
                        f'<a href="{s}" target="_blank">{s}</a>' for s in msg["sources"]
                    )
                    + "</div>",
                    unsafe_allow_html=True,
                )

# ── Input box ──────────────────────────────────────────────────────────────────
question = st.chat_input("Ask a question about OptiSigns…")

if question:
    # Display user message
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Get bot response
    with st.chat_message("assistant"):
        with st.spinner("Looking through the documentation…"):
            try:
                answer = uploader.ask_bot(client, store_name, question)
                sources = extract_sources(answer)
                st.markdown(answer)
                if sources:
                    st.markdown(
                        "<div class='source-box'><strong>📚 Sources:</strong><br>"
                        + "<br>".join(
                            f'<a href="{s}" target="_blank">{s}</a>' for s in sources
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer, "sources": sources}
                )
            except Exception as exc:
                err_msg = f"Sorry, something went wrong: {exc}"
                st.error(err_msg)
                st.session_state.messages.append(
                    {"role": "assistant", "content": err_msg, "sources": []}
                )

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### About OptiBot")
    st.markdown(
        "OptiBot answers questions using the official "
        "[OptiSigns Support Center](https://support.optisigns.com/hc/en-us) "
        "documentation.\n\n"
        "It will only answer based on uploaded support articles and will "
        "always cite its sources."
    )
    st.divider()
    if st.button("🗑️ Clear chat"):
        st.session_state.messages = []
        st.rerun()
