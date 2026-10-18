# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY f1dash ./f1dash
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    F1DASH_CACHE_DIR=/data/cache
RUN useradd --system --uid 10001 --home-dir /app f1dash \
    && mkdir -p /data/cache && chown -R f1dash /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
USER f1dash
WORKDIR /app
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
# One worker so the per-host request budget is shared; threads serve
# concurrent page panels and long-lived SSE streams.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--workers", "1", \
     "--threads", "16", "--timeout", "120", "--access-logfile", "-", "f1dash:create_app()"]
