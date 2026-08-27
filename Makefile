.PHONY: install dev run-streamlit run-api test etl-test docker-up docker-down lint format

install:
	pip install -r requirements.txt

dev:
	pip install -e ".[dev]"

run-streamlit:
	streamlit run module_3_frontend/app.py --server.address=0.0.0.0 --server.port=8501

run-api:
	uvicorn module_3_frontend.api:create_app --factory --host 0.0.0.0 --port 8000

test:
	pytest

etl-test:
	python tests/test_module1_etl.py

docker-up:
	docker compose -f docker/docker-compose.yml up -d

docker-down:
	docker compose -f docker/docker-compose.yml down

lint:
	ruff check .

format:
	black .
