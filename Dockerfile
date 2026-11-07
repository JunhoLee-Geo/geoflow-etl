FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.12 && useradd --uid 10001 --create-home geoflow
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra monitor --no-editable
COPY configs ./configs
COPY data/sample ./data/sample
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker ./docker
RUN mkdir -p /app/reports && chown -R geoflow:geoflow /app
ENV PATH="/app/.venv/bin:$PATH"
USER geoflow
ENTRYPOINT ["python", "docker/entrypoint.py"]
CMD ["etl", "validate-config", "configs/sample.yml"]
