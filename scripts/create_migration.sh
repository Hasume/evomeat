#!/bin/bash
# Скрипт для создания миграции базы данных

if [ -z "$1" ]; then
    echo "Usage: ./create_migration.sh 'migration message'"
    exit 1
fi

echo "Creating migration: $1"
alembic revision --autogenerate -m "$1"
echo "✅ Migration created!"
echo "Run 'alembic upgrade head' to apply migration"

