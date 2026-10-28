# Convenience targets for Linux. Each one just calls the scripts documented in README.md.
.PHONY: install start start-prod test lint

install:        ## system packages + Python/Node dependencies + .env
	./install.sh

start:          ## API, worker and website (development mode)
	./start.sh

start-prod:     ## API, worker and optimised website build
	./start.sh --prod

test:           ## backend test suite
	cd backend && .venv/bin/python -m pytest

lint:           ## backend lint, frontend lint and type check
	cd backend && .venv/bin/ruff check annotator tests
	cd frontend && PATH="$(CURDIR)/.tools/node/bin:$$PATH" npm run lint && PATH="$(CURDIR)/.tools/node/bin:$$PATH" npm run typecheck
