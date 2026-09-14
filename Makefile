# Dropship 개발 명령 모음
#
# 처음 받았다면:  make setup && make up && make bootstrap
# 매일 쓰는 것:   make dev-api  /  make dev-web

.DEFAULT_GOAL := help
.PHONY: help setup up down bootstrap dev-api dev-web sweep test e2e lint fmt typecheck build gen-api clean

PY := python
VENV := .venv
ifeq ($(OS),Windows_NT)
	VENV_PY := $(VENV)/Scripts/python.exe
else
	VENV_PY := $(VENV)/bin/python
endif

help:
	@echo "setup      의존성 설치 (venv + npm)"
	@echo "up         LocalStack 기동"
	@echo "down       LocalStack 정지"
	@echo "bootstrap  S3 버킷 + DynamoDB 테이블 생성"
	@echo "dev-api    FastAPI 개발 서버 (:8000)"
	@echo "dev-web    Next.js 개발 서버 (:3000)"
	@echo "sweep      만료 정리를 한 번 수동 실행"
	@echo "test       API 단위 테스트 (moto, AWS 불필요)"
	@echo "e2e        실제 presigned URL 왕복 점검 (LocalStack + dev-api 필요)"
	@echo "lint       ruff + eslint"
	@echo "typecheck  mypy + tsc"
	@echo "build      프론트 정적 빌드 (apps/web/out)"
	@echo "gen-api    OpenAPI -> TypeScript 타입 생성"

setup:
	$(PY) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e "apps/api[dev]"
	npm install
	@echo "완료. .env.example 을 .env 로 복사한 뒤 make up && make bootstrap"

up:
	docker compose up -d
	@echo "LocalStack 기동 중… 준비되면 make bootstrap"

down:
	docker compose down

bootstrap:
	$(VENV_PY) scripts/bootstrap_local.py

dev-api:
	$(VENV_PY) -m uvicorn app.main:app --reload --port 8000 --app-dir apps/api

dev-web:
	npm run dev --workspace apps/web

sweep:
	$(VENV_PY) -m app.sweeper

test:
	cd apps/api && ../../$(VENV_PY) -m pytest -q

e2e:
	$(VENV_PY) scripts/e2e_check.py

lint:
	$(VENV_PY) -m ruff check apps/api scripts
	npm run lint

fmt:
	$(VENV_PY) -m ruff format apps/api scripts
	$(VENV_PY) -m ruff check --fix apps/api scripts

typecheck:
	cd apps/api && ../../$(VENV_PY) -m mypy app
	npm run typecheck

build:
	npm run build --workspace apps/web

gen-api:
	node scripts/gen-api-types.mjs

clean:
	rm -rf apps/web/.next apps/web/out .localstack
