FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN uv sync --frozen --no-cache

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uv", "run", "main.py"]