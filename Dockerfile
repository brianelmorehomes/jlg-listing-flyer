FROM python:3.11-slim

# System libraries WeasyPrint needs for PDF/print rendering (Pango, Cairo,
# GDK-Pixbuf) -- this is exactly why we can't run this on a plain serverless
# function; a real container gives us apt-get.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000
EXPOSE 10000

# 2 workers is plenty for single-agent, once-a-day use; keep it light so it
# fits comfortably in Render's free-tier memory limit. The 300s timeout
# matters: the card-style flyer parses the sheet twice and re-renders up to
# three times to fit two pages, ~14s on a laptop but over a minute on
# Render's free-tier CPU. At 60s gunicorn killed the worker mid-render and
# the browser got an HTML error page instead of JSON.
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 300
