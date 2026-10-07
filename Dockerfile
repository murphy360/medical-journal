FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JOURNAL_DB=/data/journal.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY static ./static

RUN useradd --create-home --uid 1000 journal \
    && mkdir -p /data && chown journal:journal /data
USER journal
VOLUME /data

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz')" || exit 1

# One worker so SQLite has a single writer; threads keep it responsive while
# a Claude request is in flight.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "180", "server:app"]
