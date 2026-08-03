FROM python:3.13-slim

# PYTHONUNBUFFERED — чтобы логи попадали в docker logs сразу, а не по буферу.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: правки кода не пересобирают их каждый раз.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Бот не должен ходить под root: если его когда-нибудь скомпрометируют,
# пусть у него будут права только на свой каталог с данными.
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app
USER bot

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "app"]
