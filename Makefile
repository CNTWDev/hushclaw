.PHONY: install install-server install-all test lint clean serve

install:
	pip install -e .

install-server:
	pip install -e ".[server]"

install-anthropic:
	pip install -e ".[anthropic]"

install-all:
	pip install -e ".[all]"

test:
	python -m pytest tests/ -v

lint:
	python -m py_compile hushclaw/**/*.py

serve:
	hushclaw serve

serve-lan:
	hushclaw serve --host 0.0.0.0

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	rm -rf build dist *.egg-info .eggs
