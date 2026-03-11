.PHONY: install test lint clean

install:
	pip install -e .

install-anthropic:
	pip install -e ".[anthropic]"

install-all:
	pip install -e ".[all]"

test:
	python -m pytest tests/ -v

lint:
	python -m py_compile ghostclaw/**/*.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	rm -rf build dist *.egg-info .eggs
