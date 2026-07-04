# ── Base image ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Metadata
LABEL maintainer="OptiBot" \
      description="OptiSigns support bot — daily scraper job + Streamlit UI"

# ── System dependencies ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────────
COPY . .

# Create necessary directories (they might be absent if .gitignore excludes them)
RUN mkdir -p data/articles logs

# ── Environment ────────────────────────────────────────────────────────────────
# GEMINI_API_KEY must be supplied at runtime via -e or Render environment vars.
# Never bake secrets into the image.
ENV PYTHONUNBUFFERED=1

# ── Default command: run the daily job ────────────────────────────────────────
# Override with:
#   docker run <image> streamlit run app.py --server.port=8501 --server.address=0.0.0.0
CMD ["python", "main.py"]

# ── Render deployment notes ────────────────────────────────────────────────────
# Cron Job service  → Start Command: python main.py
# Web Service       → Start Command: streamlit run app.py --server.port=$PORT --server.address=0.0.0.0
