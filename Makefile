PY ?= .venv/bin/python

.PHONY: install run test seed eval backup

# One-time setup: create the virtual environment and install the libraries
install:
	python3.12 -m venv .venv
	.venv/bin/pip install -r requirements.txt

# Start the app at http://127.0.0.1:8000
run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Run the automated tests
test:
	$(PY) -m pytest

# Rebuild the demo client and its files
seed:
	$(PY) -m seed.demo_data

# Accuracy test: run the 20 demo orders and compare with their answer keys.
# Options go in ARGS, e.g. make eval ARGS="--only 05" or make eval ARGS="--no-cache"
eval:
	$(PY) -m eval.run $(ARGS)

# Zip the data folder into backups/
backup:
	$(PY) -m app.backup
