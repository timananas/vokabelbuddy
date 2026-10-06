FROM python:3.12-alpine

ENV TZ=Europe/Berlin \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache tzdata && \
    addgroup -S fh && adduser -S fh -G fh

WORKDIR /app
COPY server.py index.html manifest.json sw.js /app/
COPY assets /app/assets
COPY seed /app/seed
RUN mkdir -p /app/data && chown -R fh:fh /app

USER fh
EXPOSE 8894
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8894/api/health',timeout=4)"
CMD ["python3", "server.py"]