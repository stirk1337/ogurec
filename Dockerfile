FROM python:3.14-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock /app/

WORKDIR /app

COPY . /app
RUN uv sync --locked && mkdir -p /app/data

ENV UV_NO_DEV=1
ENV PATH="/app/.venv/bin:$PATH"
ENV OGUREC_DATA_DIR=/app/data

VOLUME ["/app/data"]

CMD ["ogurec"]