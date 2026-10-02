.PHONY: test lint eval eval-check eval-transfer eval-transfer-check eval-holdout eval-holdout-check smoke

test:
	cd backend && .venv/bin/python -m pytest -q tests ../evals/test_scoring.py

lint:
	cd backend && .venv/bin/ruff check app tests ../evals ../scripts/smoke_backend.py
	cd backend && .venv/bin/vulture app --min-confidence 80

eval:
	backend/.venv/bin/python evals/run_live.py

eval-check:
	backend/.venv/bin/python evals/run_live.py --check

eval-transfer:
	backend/.venv/bin/python evals/run_transfer.py

eval-transfer-check:
	backend/.venv/bin/python evals/run_transfer.py --check

eval-holdout:
	backend/.venv/bin/python evals/run_transfer.py --suite holdout

eval-holdout-check:
	backend/.venv/bin/python evals/run_transfer.py --suite holdout --check

smoke:
	backend/.venv/bin/python scripts/smoke_backend.py
