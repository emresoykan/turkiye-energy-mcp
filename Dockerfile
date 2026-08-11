FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY turkiye_energy_mcp ./turkiye_energy_mcp

RUN pip install --upgrade pip && pip install .

ENV HOST=0.0.0.0 \
    PORT=8000 \
    MCP_TRANSPORT=streamable-http

EXPOSE 8000

CMD ["turkiye-energy-mcp"]
