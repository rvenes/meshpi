FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --system --home-dir /app --shell /usr/sbin/nologin meshpi

COPY . /app
RUN python -m pip install --no-cache-dir .

RUN mkdir -p /data && chown meshpi:meshpi /data

USER meshpi

CMD ["meshpi", "daemon"]

