FROM python:3.12-slim

LABEL maintainer="kb-sync-bot" description="Support knowledge bot — daily scraper job + Streamlit UI"

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    mkdir -p data/articles logs

COPY . .

# Default command (override for web service: streamlit run app.py ...)
CMD ["python", "main.py"]
