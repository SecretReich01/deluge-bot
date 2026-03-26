FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + required OS deps for Playwright
RUN python -m playwright install --with-deps chromium

COPY . /app

CMD ["python", "bot.py"]
