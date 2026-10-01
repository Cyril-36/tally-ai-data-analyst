.PHONY: test lint eval eval-check smoke

test:
	cd backend && .venv/bin/python -m pytest -q tests ../evals/test_scoring.py

lint:
	cd backend && .venv/bin/ruff check app tests ../evals ../scripts/smoke_backend.py
	cd backend && .venv/bin/vulture app --min-confidence 80

eval:
	backend/.venv/bin/python evals/run_live.py

eval-check:
	backend/.venv/bin/python evals/run_live.py --check

smoke:
	backend/.venv/bin/python scripts/smoke_backend.py
