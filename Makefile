.PHONY: up down reset psql-dwh logs inspect

up:
	docker compose up -d

down:
	docker compose down

reset:
	docker compose down -v && docker compose up -d

psql-dwh:
	docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh

logs:
	docker compose logs -f

inspect:
	python scripts/inspect_minio_data.py

restart:
	docker compose restart

status:
	docker compose ps
