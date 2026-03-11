.PHONY: help install run run-api run-docker test lint format clean migrate

help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make run        - Run the bot"
	@echo "  make run-api    - Run FastAPI server"
	@echo "  make run-docker - Run with Docker Compose"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Run linter"
	@echo "  make format     - Format code with black"
	@echo "  make clean      - Clean temporary files"
	@echo "  make migrate    - Create and apply database migration"

install:
	pip install -r requirements.txt

run:
	python main.py

run-api:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

run-docker:
	docker-compose up --build

test:
	pytest

test-coverage:
	pytest --cov=. --cov-report=html

lint:
	flake8 .
	mypy .

format:
	black .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage

migrate:
	@read -p "Enter migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"
	alembic upgrade head

init-db:
	python scripts/init_db.py

docker-logs:
	docker-compose logs -f bot

docker-stop:
	docker-compose down

docker-restart:
	docker-compose restart

docker-clean:
	docker-compose down -v
	docker system prune -f

