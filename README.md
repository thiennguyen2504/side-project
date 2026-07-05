# Support KB Sync Bot

Daily-sync pipeline that scrapes a Zendesk Help Center, stores articles in a Gemini File Search Store, and answers support questions with cited sources.

## Setup

```bash
git clone https://github.com/thiennguyen2504/side-project.git && cd side-project
pip install -r requirements.txt
cp .env.sample .env   # add your GEMINI_API_KEY
```
Get a free key at [Google AI Studio](https://aistudio.google.com/app/apikey).

## Run Locally

| Task | Command |
|---|---|
| Daily job (scrape + delta upload) | `python main.py` |
| Chat UI | `streamlit run app.py` |
| Tests | `pytest tests/ -v` |

Job logs print `added=X, updated=Y, skipped=Z` to stdout and `logs/run_<timestamp>.log`.

## Run with Docker

```bash
docker build -t chatbot .
docker run -e GEMINI_API_KEY=your_key chatbot          # runs main.py once, exits 0
docker run -e GEMINI_API_KEY=your_key -p 8501:8501 \
  chatbot streamlit run app.py --server.port=8501 --server.address=0.0.0.0
```

## Architecture

```
scraper.py → clean Markdown  →  main.py (SHA-256 delta) → uploader.py (Gemini File Search Store) → app.py (chat UI)
```

**Chunking strategy:** one file = one article, ATX headings preserved, `Article URL:` placed on line 3 so it survives truncation. Gemini's File Search Store auto-chunks and embeds each document (`gemini-embedding-001`); we don't control chunk size directly but keep boundaries clean by uploading one article per file.

**Delta logic:** We encode an 8-character SHA-256 hash prefix directly into each uploaded document's display name (`{slug}__{hash8}.md`). On every run, we list the store's current contents to figure out what's already uploaded. Unchanged articles are skipped; changed articles (slug exists but hash differs) are deleted then re-uploaded. Because there is no local `state.json` or state file, this job is entirely disk-free and safe to run in fully ephemeral containers (like Render Cron Jobs).

## Daily Job Deployment

Deployed on Render as a Cron Job (see deployment guide). Latest run log: <img width="2559" height="1525" alt="image" src="https://github.com/user-attachments/assets/3b87c71d-2a16-41a2-8d4f-b8730c7a519d" />


## Sample Query & Screenshot

> "How do I add a YouTube video?"

<img width="2542" height="1513" alt="image" src="https://github.com/user-attachments/assets/9c8cf726-30fb-46b2-be14-9f15ce1a720a" />


## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio API key |
