# OptiBot — OptiSigns Support Knowledge Bot

> A daily-sync pipeline that scrapes the OptiSigns Zendesk Help Center, stores articles in a Gemini File Search Store, and answers support questions via a Streamlit chat UI.

---

## Quick Setup

```bash
git clone https://github.com/thiennguyen2504/side-project.git && cd side-project
pip install -r requirements.txt
cp .env.sample .env          # then add your key
# Windows:  set GEMINI_API_KEY=your_key
# Linux/Mac: export GEMINI_API_KEY=your_key
```

Get a free API key at [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## Running Locally

### 1. Daily job (scrape + upload delta)
```bash
python main.py
```
Logs are written to `logs/run_<timestamp>.log` and stdout.  
Output format: `added=X, updated=Y, skipped=Z`

### 2. Streamlit UI
```bash
streamlit run app.py
```
Open `http://localhost:8501` in your browser.

### 3. Unit tests
```bash
pytest tests/ -v
```

---

## Running with Docker

### Build
```bash
docker build -t optibot .
```

### Daily job (default CMD)
```bash
docker run -e GEMINI_API_KEY=your_key optibot
```
Runs `main.py` once and exits with code 0.

### Streamlit UI
```bash
docker run -e GEMINI_API_KEY=your_key -p 8501:8501 \
  optibot streamlit run app.py --server.port=8501 --server.address=0.0.0.0
```

---

## Deploying on Render

Two services from **the same Docker image / repo**:

| Service type | Start command |
|---|---|
| **Cron Job** | `python main.py` |
| **Web Service** | `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0` |

Set `GEMINI_API_KEY` as an environment variable in both Render services.

---

## Architecture

```
scraper.py          Zendesk Help Center API → clean Markdown files
     ↓
main.py             SHA-256 hash diff → delta upload only
     ↓
uploader.py         Gemini File Search Store (create / upload / delete)
     ↓
app.py              Streamlit chat UI → ask_bot() → grounded response
```

### Chunking Strategy

Files are uploaded as plain-text Markdown (one file per article).  
Gemini automatically **chunks and embeds** each document using `gemini-embedding-001`.

We influence chunk quality indirectly by:
- **One file = one article** — keeps chunk boundaries clean.
- **ATX headings preserved** (`##`, `###`) — the model uses them as section anchors.
- **`Article URL:` on line 3** (before any heading) — ensures it is always within the first chunk and survives any truncation.

### Hash & Delta Logic

Each article is hashed with **SHA-256 after whitespace normalisation**:
1. Trim each line.
2. Collapse internal runs of spaces to one.
3. Collapse 2+ blank lines to one.
4. Hash the result with SHA-256.

This means cosmetic reformatting (trailing spaces, extra blank lines) does **not** trigger a re-upload, but any real content change does.

State is persisted in `state.json`:
```json
{
  "how-to-use-youtube-with-optisigns.md": {
    "hash": "abc123...",
    "document_name": "fileSearchStores/xyz/documents/doc1",
    "updated_at": "2025-01-01T00:00:00+00:00"
  }
}
```

On update: old document is **deleted first**, then new version uploaded — prevents duplicate content in search results.

---

## Project Structure

```
side-project/
├── main.py              # Daily job entrypoint
├── scraper.py           # Zendesk API + HTML→Markdown
├── uploader.py          # Gemini File Search Store logic
├── app.py               # Streamlit UI
├── requirements.txt
├── Dockerfile
├── .env.sample
├── README.md
├── data/articles/*.md   # Scraped articles (gitignored)
├── logs/                # Run logs (gitignored)
├── tests/
│   └── test_hash_diff.py
└── state.json           # Delta state (gitignored)
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio API key |
