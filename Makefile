STACK_NAME=nicodemus-adm
IMAGE_NAME=nicodemus-adm
SHELL := /bin/bash

.PHONY: help build deploy update down logs logs-redis ps status restart shell migrate

help:
	@echo ""
	@echo "  make build        → constrói a imagem Docker (sem cache)"
	@echo "  make deploy       → build + deploy no Swarm"
	@echo "  make update       → redeploy sem rebuild (usa imagem existente)"
	@echo "  make down         → remove o stack"
	@echo "  make logs         → logs da app em tempo real"
	@echo "  make logs-redis   → logs do nico_redis"
	@echo "  make ps           → status das tasks no Swarm"
	@echo "  make status       → serviços do stack"
	@echo "  make restart      → down + deploy"
	@echo "  make shell        → bash no container em execução"
	@echo "  make migrate      → roda alembic upgrade head no container"
	@echo ""

build:
	docker build --no-cache -t $(IMAGE_NAME):latest .

deploy: build
	@set -a; \
	. <(sed 's/[[:space:]]*#[^"]*$$//; /^[[:space:]]*$$/d' .env); \
	set +a; \
	docker stack deploy -c swarm-stack.yml $(STACK_NAME)

update:
	@set -a; \
	. <(sed 's/[[:space:]]*#[^"]*$$//; /^[[:space:]]*$$/d' .env); \
	set +a; \
	docker stack deploy -c swarm-stack.yml $(STACK_NAME)

down:
	docker stack rm $(STACK_NAME)

logs:
	docker service logs $(STACK_NAME)_app --follow --tail 100

logs-redis:
	docker service logs $(STACK_NAME)_nico_redis --follow --tail 50

ps:
	docker stack ps $(STACK_NAME)

status:
	docker stack services $(STACK_NAME)

restart: down
	@sleep 5
	@$(MAKE) deploy

shell:
	docker exec -it $$(docker ps -q -f name=$(STACK_NAME)_app) bash

migrate:
	docker exec $$(docker ps -q -f name=$(STACK_NAME)_app) alembic upgrade head