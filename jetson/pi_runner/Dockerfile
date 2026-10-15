# 构建上下文必须是仓库根目录。
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM --platform=linux/arm64 node@sha256:f71fb9ca71b1b47d4d1a009af78147ed6cdc74f9c7cfc36cbde78ff985169051 AS node
FROM ${PYTHON_BASE}
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/lib/aarch64-linux-gnu/libstdc++.so.6* /usr/lib/aarch64-linux-gnu/
COPY --from=node /usr/lib/aarch64-linux-gnu/libgcc_s.so.1 /usr/lib/aarch64-linux-gnu/libgcc_s.so.1
COPY shared/ /opt/uav/app/shared/
COPY jetson/__init__.py /opt/uav/app/jetson/__init__.py
COPY jetson/edge_agent/ /opt/uav/app/jetson/edge_agent/
COPY jetson/pi_runner/ /opt/uav/app/jetson/pi_runner/
COPY uav_rag/__init__.py /opt/uav/app/uav_rag/__init__.py
COPY uav_rag/client.py /opt/uav/app/uav_rag/client.py
COPY uav_rag/tools.py /opt/uav/app/uav_rag/tools.py
COPY uav_rag/catalog.py /opt/uav/app/uav_rag/catalog.py
COPY uav_rag/legacy_catalog.py /opt/uav/app/uav_rag/legacy_catalog.py
COPY uav_rag/retrieval.py /opt/uav/app/uav_rag/retrieval.py
COPY uav_rag/mcp.py /opt/uav/app/uav_rag/mcp.py
COPY uav_rag/mcp_guard.py /opt/uav/app/uav_rag/mcp_guard.py
COPY deploy/__init__.py /opt/uav/app/deploy/__init__.py
COPY deploy/scripts/__init__.py /opt/uav/app/deploy/scripts/__init__.py
COPY deploy/scripts/initialize_control.py /opt/uav/app/deploy/scripts/initialize_control.py
RUN pip install --no-cache-dir --target /opt/uav/vendor -r /opt/uav/app/jetson/pi_runner/requirements.txt
RUN mkdir -p /state /pi-state /run/uav-pi /home/node /workspace/project && chown -R 1000:1000 /state /pi-state /run/uav-pi /home/node /workspace/project
ENV PYTHONPATH=/opt/uav/vendor:/opt/uav/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV HOME=/home/node USER=node LOGNAME=node
WORKDIR /opt/uav/app
USER 1000:1000
ENTRYPOINT ["python", "-m", "jetson.pi_runner"]
