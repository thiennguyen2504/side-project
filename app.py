"""
app.py — Streamlit web UI for the support knowledge bot.

Features:
  - Single text input for user questions.
  - Displays bot answer and extracted source URLs (Article URL citations).
  - Loads the Gemini File Search Store on startup.
  - Shows clear error messages if the API key is missing.
"""

# Load .env file first — must happen before any other import that reads env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os
import re
import logging
import streamlit as st

import uploader

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Support KB Bot",
    page_icon="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F4AC;</text></svg>",
    layout="centered",
)

logging.basicConfig(level=logging.INFO)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
# Palette:
#   #111827  near-black — primary text, headers
#   #F9F8F6  warm off-white — page background
#   #1D4ED8  deep institutional blue — links only
#   #6B7280  cool mid-gray — metadata, secondary text
#
# Fonts:
#   IBM Plex Sans — headers, labels (mechanical, engineering-tool feel)
#   Source Sans 3 — body, chat (legible, neutral, clinical)
#
# Signature choice:
#   Source citations rendered as document footnotes: a thin 1px rule + spaced
#   small-caps label, no background fill, no icon — every boundary is a line.

st.markdown(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Source+Sans+3:wght@400;600&display=swap" rel="stylesheet">

    <style>
    html, body, [class*="css"] {
        font-family: 'Source Sans 3', sans-serif;
        color: #111827;
    }

    .stApp {
        background-color: #F9F8F6;
    }

    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 760px;
    }

    h1, h2, h3, h4,
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        font-family: 'IBM Plex Sans', sans-serif;
        font-weight: 600;
        color: #111827;
        letter-spacing: -0.01em;
    }

    .stMarkdown h1 {
        font-size: 1.35rem;
        border-bottom: 1.5px solid #111827;
        padding-bottom: 0.4rem;
        margin-bottom: 0.15rem;
    }

    .stCaption p,
    [data-testid="stCaptionContainer"] p {
        font-size: 0.8rem;
        color: #6B7280;
        letter-spacing: 0.01em;
    }

    [data-testid="stChatMessage"] {
        background: transparent;
        border: none;
        padding: 0.5rem 0;
        border-top: 1px solid #E5E7EB;
    }

    [data-testid="stChatMessage"] p,
    [data-testid="stChatMessage"] li {
        font-family: 'Source Sans 3', sans-serif;
        font-size: 0.95rem;
        line-height: 1.65;
        color: #111827;
    }

    /* Source citations: document footnote style */
    .source-block {
        margin-top: 0.9rem;
        padding-top: 0.65rem;
        border-top: 1px solid #D1D5DB;
    }

    .source-label {
        display: block;
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 0.65rem;
        font-weight: 600;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #6B7280;
        margin-bottom: 0.35rem;
    }

    .source-block a {
        display: block;
        font-size: 0.82rem;
        color: #1D4ED8;
        text-decoration: underline;
        text-underline-offset: 2px;
        word-break: break-all;
        line-height: 1.6;
    }

    .source-block a:hover {
        color: #1e3a8a;
    }

    [data-testid="stAlert"] {
        border-radius: 0;
        border-left: 2px solid #DC2626;
        background: transparent;
        padding: 0.6rem 0.8rem;
    }

    [data-testid="stSidebar"] {
        background-color: #F3F2F0;
        border-right: 1px solid #E5E7EB;
    }

    [data-testid="stSidebar"] h3 {
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #6B7280;
        margin-bottom: 0.5rem;
    }

    [data-testid="stSidebar"] p {
        font-size: 0.82rem;
        color: #374151;
        line-height: 1.6;
    }

    [data-testid="stSidebar"] button[kind="secondary"] {
        background: transparent;
        border: 1px solid #D1D5DB;
        border-radius: 2px;
        color: #374151;
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 0.82rem;
        padding: 0.3rem 0.75rem;
        width: 100%;
    }

    [data-testid="stSidebar"] button[kind="secondary"]:hover {
        background: #E9E8E5;
        border-color: #9CA3AF;
    }

    [data-testid="stSidebar"] button[kind="secondary"]:focus-visible {
        outline: 2px solid #1D4ED8;
        outline-offset: 2px;
    }

    [data-testid="stChatInput"] textarea {
        font-family: 'Source Sans 3', sans-serif;
        font-size: 0.95rem;
        border-radius: 2px;
        border: 1px solid #D1D5DB;
        background: #FFFFFF;
    }

    [data-testid="stChatInput"] textarea:focus {
        border-color: #111827;
        box-shadow: none;
        outline: none;
    }

    hr {
        border: none;
        border-top: 1px solid #E5E7EB;
        margin: 0.8rem 0;
    }

    [data-testid="stSpinner"] p {
        font-size: 0.82rem;
        color: #6B7280;
    }

    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"] {
        background: #E5E7EB;
        color: #374151;
        font-size: 0.7rem;
        font-family: 'IBM Plex Sans', sans-serif;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("Support Knowledge Base")
st.caption("Answers drawn from uploaded documentation. Verify critical information against the cited source articles.")
st.divider()


def extract_sources(text: str) -> list[str]:
    """
    Extract Article URLs from the bot's response text.

    Only matches URLs that appear immediately after "Article URL:" —
    case-insensitive, optional whitespace between ':' and URL.

    Returns:
        Deduplicated list of source URL strings (order preserved).
    """
    sources = re.findall(
        r"(?i)Article\s+URL:\s*(https?://[^\s\)\]>\"']+)",
        text,
    )
    return list(dict.fromkeys(sources))  # deduplicate, preserve order


def render_sources(sources: list[str]) -> None:
    """Render source citations as flat document-footnote-style list."""
    if not sources:
        return
    links = "".join(
        f'<a href="{s}" target="_blank" rel="noopener noreferrer">{s}</a>'
        for s in sources
    )
    st.markdown(
        f"<div class='source-block'>"
        f"<span class='source-label'>Sources</span>"
        f"{links}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ── Session state: load client & store once ────────────────────────────────────

@st.cache_resource(show_spinner="Connecting to knowledge base.")
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
        "**Configuration error:** `GEMINI_API_KEY` is not set in the environment.  \n"
        "Set the variable before starting the app:\n"
        "```bash\nexport GEMINI_API_KEY=your_key_here\n```"
    )
    st.stop()

try:
    client, store_name = load_bot()
except Exception as exc:
    st.error(f"**Startup error:** Could not connect to the knowledge base. {exc}")
    st.stop()

# ── Chat history ───────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

# Render previous messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            render_sources(msg["sources"])

# ── Input box ──────────────────────────────────────────────────────────────────
question = st.chat_input("Enter your question.")

if question:
    # Display user message
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Get bot response
    with st.chat_message("assistant"):
        with st.spinner("Retrieving answer."):
            try:
                answer = uploader.ask_bot(client, store_name, question)
                sources = extract_sources(answer)
                st.markdown(answer)
                render_sources(sources)
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer, "sources": sources}
                )
            except Exception as exc:
                err_msg = f"The request failed. Details: {exc}"
                st.error(err_msg)
                st.session_state.messages.append(
                    {"role": "assistant", "content": err_msg, "sources": []}
                )

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### About")
    st.markdown(
        "Answers are grounded in the uploaded support documentation. "
        "The bot will not speculate outside that corpus, and it cites the source article for each claim."
    )
    st.divider()
    if st.button("Clear chat", key="clear_chat_btn"):
        st.session_state.messages = []
        st.rerun()
