FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --system --home-dir /app --shell /usr/sbin/nologin meshpi

COPY locks/linux.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

COPY pyproject.toml README.md LICENSE /app/
COPY meshpi /app/meshpi
RUN python -m pip install --no-cache-dir --no-deps .

RUN mkdir -p /data && chown meshpi:meshpi /data

USER meshpi

CMD ["meshpi", "daemon"]
