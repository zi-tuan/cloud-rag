# 构建上下文必须是仓库根目录。
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}
COPY shared/ /opt/uav/app/shared/
COPY jetson/__init__.py /opt/uav/app/jetson/__init__.py
COPY jetson/edge_agent/ /opt/uav/app/jetson/edge_agent/
COPY jetson/pi_runner/ /opt/uav/app/jetson/pi_runner/
COPY deploy/__init__.py /opt/uav/app/deploy/__init__.py
COPY deploy/scripts/__init__.py /opt/uav/app/deploy/scripts/__init__.py
COPY deploy/scripts/initialize_control.py /opt/uav/app/deploy/scripts/initialize_control.py
RUN pip install --no-cache-dir --target /opt/uav/vendor -r /opt/uav/app/jetson/edge_agent/requirements.txt
RUN mkdir -p /state /pi-state /run/uav-pi /home/node /workspace/project && chown -R 1000:1000 /state /pi-state /run/uav-pi /home/node /workspace/project
ENV PYTHONPATH=/opt/uav/vendor:/opt/uav/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/uav/app
USER 1000:1000
ENTRYPOINT ["python", "-m", "jetson.edge_agent.runtime"]
