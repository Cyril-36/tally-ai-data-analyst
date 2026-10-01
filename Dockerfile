FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    DATA_DIR=/srv/app/var

RUN pip install --no-cache-dir uv==0.8.8 \
    && groupadd --system analyst \
    && useradd --system --gid analyst --home-dir /srv/app analyst

WORKDIR /srv/app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
RUN uv sync --frozen --no-dev \
    && mkdir -p /srv/app/var \
    && chown analyst:analyst /srv/app/var

USER analyst
ENV PATH=/srv/app/.venv/bin:$PATH
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=2)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
