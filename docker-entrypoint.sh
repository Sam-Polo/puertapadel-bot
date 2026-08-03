#!/bin/sh
# Миграции применяются на каждом старте: контейнер должен подниматься
# на любой версии схемы без ручных шагов.
set -e

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> starting bot"
exec "$@"
