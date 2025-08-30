FROM ubuntu:24.04

# Install Python 3.12 and system deps
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-pip ca-certificates curl tzdata build-essential \
        && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python3.12 -m venv /opt/venv

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Create data dir for SQLite/volumes
RUN mkdir -p /data && chmod 755 /data

EXPOSE 8080 8081

ENTRYPOINT ["python3.12", "-m", "teltubby.main"]

