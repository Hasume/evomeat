#!/bin/bash
# Скрипт запуска через Docker

echo "🐳 Starting NutritionBot with Docker..."

# Проверка .env файла
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Copy .env.example to .env and fill in your credentials"
    exit 1
fi

# Остановка старых контейнеров
echo "🛑 Stopping old containers..."
docker-compose down

# Сборка и запуск
echo "🔨 Building and starting containers..."
docker-compose up --build -d

# Показать логи
echo "📋 Showing logs (Ctrl+C to exit)..."
docker-compose logs -f bot

