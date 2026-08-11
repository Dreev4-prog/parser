FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium is required only for the optional public view-count test.
RUN playwright install --with-deps chromium
COPY . .

CMD ["python", "bot.py"]
