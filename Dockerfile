FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 REFLEXBOT_DATA_DIR=/app/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[haiku,dev]"
COPY config ./config
COPY tests ./tests
RUN useradd --create-home bot && mkdir -p /app/data && chown -R bot:bot /app
USER bot
ENTRYPOINT ["python", "-m", "reflexbot"]
CMD ["run"]
