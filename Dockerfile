FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# IMPORTANT: Clear any DISPLAY variable inherited from the base image or host.
# SeleniumBase will set this itself when xvfb=True is used.
ENV DISPLAY=

WORKDIR /app

# ------------------------------------------------------------------
# 1. System Dependencies (Xvfb and essential libraries for Chrome)
# ------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    xauth \
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
# 2. Install Google Chrome (stable)
# ------------------------------------------------------------------
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome

# ------------------------------------------------------------------
# 3. Python Dependencies
# ------------------------------------------------------------------
COPY core/ /app/core/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/core/requirements.txt

# CRITICAL for xvfb=True: Install pyvirtualdisplay
RUN pip install --no-cache-dir pyvirtualdisplay

# ------------------------------------------------------------------
# 4. Install SeleniumBase's Undetected Chromedriver
# ------------------------------------------------------------------
RUN seleniumbase install chromedriver \
    && seleniumbase install uc_driver

# ------------------------------------------------------------------
# 5. Prepare the X11 Socket Directory
# ------------------------------------------------------------------
# Ensure the directory for X11 sockets exists with correct permissions.
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

# Do NOT wrap the entrypoint with xvfb-run. SeleniumBase handles it.
CMD ["python", "-u", "core/bot_backup.py"]
