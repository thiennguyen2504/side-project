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
docker build -t optibot .
docker run -e GEMINI_API_KEY=your_key optibot          # runs main.py once, exits 0
docker run -e GEMINI_API_KEY=your_key -p 8501:8501 \
  optibot streamlit run app.py --server.port=8501 --server.address=0.0.0.0
```

## Architecture

```
scraper.py → clean Markdown  →  main.py (SHA-256 delta) → uploader.py (Gemini File Search Store) → app.py (chat UI)
```

**Chunking strategy:** one file = one article, ATX headings preserved, `Article URL:` placed on line 3 so it survives truncation. Gemini's File Search Store auto-chunks and embeds each document (`gemini-embedding-001`); we don't control chunk size directly but keep boundaries clean by uploading one article per file.

**Delta logic:** SHA-256 hash of whitespace-normalized content, stored in `state.json`. Unchanged articles are skipped; changed articles are deleted then re-uploaded to avoid duplicate content.

## Daily Job Deployment

Deployed on Render as a Cron Job (see deployment guide). Latest run log: **[add your Render log link or screenshot here]**

## Sample Query & Screenshot

> "How do I add a YouTube video?"

![Sample answer](docs/sample_answer.png)

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio API key |