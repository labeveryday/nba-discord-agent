FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim

# Non-root user
RUN groupadd -r agent && useradd -r -g agent -d /app -s /usr/sbin/nologin agent

COPY --from=builder /install /usr/local
WORKDIR /app
COPY src/ ./src/
RUN mkdir -p /app/data && chown agent:agent /app/data

USER agent

ENTRYPOINT ["python", "src/agent.py"]
