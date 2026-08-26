# syntax=docker/dockerfile:1.7
# Paper-trading bot — mirrors swedish-democratic-tracker container pattern:
# multi-stage builder, non-root UID 1000, baked-in healthcheck.
FROM python:3.12-slim AS builder

WORKDIR /build
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# deps first (cache layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# app code
COPY hedge_fund ./hedge_fund
COPY scripts ./scripts
COPY pyproject.toml README.md ./

# compile check
RUN python -m compileall -q hedge_fund scripts && echo "compile OK"

# ── runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
RUN groupadd -g 1000 appgroup && useradd -m -u 1000 -g appgroup appuser

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /build/hedge_fund /app/hedge_fund
COPY --from=builder /build/scripts /app/scripts
COPY --from=builder /build/pyproject.toml /app/pyproject.toml

RUN mkdir -p /app/state && chown -R appuser:appgroup /app
USER appuser:appgroup

EXPOSE 8787
ENV PAPER_STATE=/app/state

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8787/healthz')" || exit 1

ENTRYPOINT ["python", "-m", "hedge_fund.web.server", "--host", "0.0.0.0", "--port", "8787"]