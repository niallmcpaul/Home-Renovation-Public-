FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends rclone && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

RUN useradd -m -u 1000 appuser && mkdir -p /data && chown -R appuser:appuser /srv /data
USER appuser

ENV DATA_DIR=/data
VOLUME /data

CMD ["python", "-m", "app.main"]
