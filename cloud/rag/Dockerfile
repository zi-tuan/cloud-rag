# Build context is the unified project root. Database is a versioned external mount.
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}
COPY uav_rag/ /opt/uav/app/uav_rag/
WORKDIR /opt/uav/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
ENTRYPOINT ["python", "-m", "uav_rag", "serve", "--host", "0.0.0.0", "--db", "/data/openssl.sqlite3"]
