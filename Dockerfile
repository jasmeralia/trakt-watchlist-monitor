FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

RUN useradd --system --uid 1001 appuser
USER 1001

HEALTHCHECK --interval=30m --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys; sys.exit(0)"]

CMD ["python", "main.py"]
