# 构建上下文必须是仓库根目录。
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}
COPY shared/ /app/shared/
COPY cloud/__init__.py /app/cloud/__init__.py
COPY cloud/cloud_api/ /app/cloud/cloud_api/
COPY deploy/__init__.py /app/deploy/__init__.py
COPY deploy/scripts/__init__.py /app/deploy/scripts/__init__.py
COPY deploy/scripts/initialize_control.py /app/deploy/scripts/initialize_control.py
RUN pip install --no-cache-dir --target /app/vendor -r /app/cloud/cloud_api/requirements.txt
RUN mkdir -p /state /pi-state /run/uav-pi /home/node /workspace/project && chown -R 1000:1000 /state /pi-state /run/uav-pi /home/node /workspace/project
ENV PYTHONPATH=/app/vendor:/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 1000:1000
EXPOSE 38443
ENTRYPOINT ["python", "-m", "cloud.cloud_api.api"]
