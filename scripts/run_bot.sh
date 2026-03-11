#!/bin/bash
# Скрипт запуска бота

echo "🚀 Starting NutritionBot..."

# Проверка .env файла
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Copy .env.example to .env and fill in your credentials"
    exit 1
fi

# Проверка виртуального окружения
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Активация виртуального окружения
source venv/bin/activate

# Установка зависимостей
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Инициализация базы данных
echo "🗄️ Initializing database..."
python scripts/init_db.py

# Запуск бота
echo "▶️ Starting bot..."
python main.py

