FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/packages:/app/apps
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "sqlalchemy>=2.0" "pydantic>=2" "httpx>=0.27" "joserfc>=1.0" "cryptography>=42" "pyyaml>=6" "psycopg[binary]>=3.1"
COPY packages ./packages
COPY apps ./apps
COPY fixtures ./fixtures
COPY migrations ./migrations
COPY scripts ./scripts
