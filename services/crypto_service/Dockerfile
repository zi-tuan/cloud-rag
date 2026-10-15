# 构建上下文必须是仓库根目录。
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}
COPY shared/ /app/shared/
COPY services/__init__.py /app/services/__init__.py
COPY services/crypto_service/ /app/services/crypto_service/
RUN pip install --no-cache-dir --target /app/vendor -r /app/services/crypto_service/requirements.txt
RUN mkdir -p /state /pi-state /run/uav-pi /home/node /workspace/project && chown -R 1000:1000 /state /pi-state /run/uav-pi /home/node /workspace/project
ENV PYTHONPATH=/app/vendor:/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["python", "-m", "services.crypto_service"]
