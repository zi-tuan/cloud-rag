# 构建上下文必须是仓库根目录。
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}
COPY shared/ /app/shared/
COPY services/__init__.py /app/services/__init__.py
COPY services/auth_service/ /app/services/auth_service/
COPY deploy/__init__.py /app/deploy/__init__.py
COPY deploy/scripts/__init__.py /app/deploy/scripts/__init__.py
COPY deploy/scripts/provision_identity.py /app/deploy/scripts/provision_identity.py
COPY deploy/scripts/provision_certificate_identity.py /app/deploy/scripts/provision_certificate_identity.py
RUN pip install --no-cache-dir --target /app/vendor -r /app/services/auth_service/requirements.txt
RUN mkdir -p /state /pi-state /run/uav-pi /home/node /workspace/project && chown -R 1000:1000 /state /pi-state /run/uav-pi /home/node /workspace/project
ENV PYTHONPATH=/app/vendor:/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["python", "-m", "services.auth_service"]
