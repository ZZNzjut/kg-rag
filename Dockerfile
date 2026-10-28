FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KGRAG_DATA=/app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x scripts/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["sh", "scripts/docker-entrypoint.sh"]