FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ------------------------------------------------------------------
# 1. System dependencies (Xvfb, Chrome runtime libraries, helpers)
# ------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    xdotool \
    python3-tk \
    python3-dev \
    libgbm-dev \
    fonts-liberation \
    ca-certificates \
    wget \
    curl \
    unzip \
    gnupg \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libgtk-3-0 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    libappindicator3-1 \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------
# 2. Install Google Chrome (stable) — required by SeleniumBase uc=True
# ------------------------------------------------------------------
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome

# ------------------------------------------------------------------
# 3. Python dependencies
# ------------------------------------------------------------------
COPY core/ /app/core/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/core/requirements.txt

# ------------------------------------------------------------------
# 4. Install SeleniumBase's uc_driver (undetected chromedriver)
# ------------------------------------------------------------------
RUN seleniumbase install chromedriver \
    && seleniumbase install uc_driver

# ------------------------------------------------------------------
# 5. (Optional) Playwright Chromium — kept from your original setup
# ------------------------------------------------------------------
RUN playwright install chromium --with-deps

# SeleniumBase manages its own Xvfb when xvfb=True, so we do NOT
# wrap the entrypoint with xvfb-run.
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24x24", \
     "python", "-u", "core/bot_backup.py"]
