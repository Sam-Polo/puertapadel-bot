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

# Работаем под root — один сервер, один бот, а каталог data монтируется
# с хоста bind-mount'ом: под непривилегированным пользователем внутри
# контейнера в него не записать без возни с chown на хосте.
RUN mkdir -p /app/data

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "app"]
