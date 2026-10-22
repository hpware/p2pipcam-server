# Docker Hub is blocked from the server; use a mirror
FROM dockerpull.org/library/python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
CMD ["python3", "p2p_server.py", "--public-host", "10.99.3.16"]
