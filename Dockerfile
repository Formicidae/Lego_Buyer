# Playwright's image ships Chromium + fonts, which the PDF checklist needs.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Railway sets $PORT; the volume is mounted at /data.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
