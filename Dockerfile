FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y \
    xvfb \
    x11-utils \
    xdotool \
    python3-tk \
    python3-dev \
    libgbm-dev \
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

ENTRYPOINT ["xvfb-run", "-a"]
CMD ["python", "core/bot_backup.py"]