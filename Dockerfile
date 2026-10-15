FROM python:3.12-slim
WORKDIR /app
COPY uav_rag /app/uav_rag
USER 1000:1000
EXPOSE 8787
CMD ["python", "-m", "uav_rag", "serve", "--host", "0.0.0.0", "--db", "/data/openssl.sqlite3"]
