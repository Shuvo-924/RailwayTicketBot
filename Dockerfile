FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    xdotool \
    python3-tk \
    python3-dev \
    libgbm-dev \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY core/ /app/core/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/core/requirements.txt

RUN playwright install chromium --with-deps

# SeleniumBase manages its own Xvfb — do NOT wrap with xvfb-run.
CMD ["python", "-u", "core/bot_backup.py"]
