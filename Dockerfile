FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV VIEW_MODE=auto
ENV REQUEST_DELAY_SECONDS=2.0
ENV BROWSER_WAIT_MS=2500

CMD ["python", "main.py", "--help"]
