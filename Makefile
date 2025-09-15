.PHONY: help build up down logs clean test format lint init

help:
	@echo "Available commands:"
	@echo "  make init      - Initialize project (copy .env.example to .env)"
	@echo "  make build     - Build all Docker containers"
	@echo "  make up        - Start all services"
	@echo "  make down      - Stop all services"
	@echo "  make logs      - Show logs from all services"
	@echo "  make clean     - Remove containers and volumes"
	@echo "  make test      - Run tests"
	@echo "  make format    - Format code with black"
	@echo "  make lint      - Lint code with ruff"

init:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env file - please update with your credentials"; \
	else \
		echo ".env file already exists"; \
	fi

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	docker compose down -v
	rm -rf storage/plans/* storage/reports/* storage/logs/* storage/orders/* storage/fills/*

test:
	pytest tests/

format:
	black .

lint:
	ruff check .

# Development shortcuts
api-logs:
	docker compose logs -f api

web-logs:
	docker compose logs -f web

broker-logs:
	docker compose logs -f broker

scheduler-logs:
	docker compose logs -f scheduler

restart:
	docker compose restart

status:
	docker compose ps

# Database operations
db-shell:
	docker compose exec db psql -U trader -d trader

db-reset:
	docker compose exec db psql -U trader -d trader -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# Testing endpoints
test-health:
	curl http://localhost:8000/health

test-run:
	curl -X POST http://localhost:8000/run -H "Content-Type: application/json" -d '{}'

test-positions:
	curl http://localhost:8000/positions

test-plan:
	curl http://localhost:8000/plan/latest