# halo-monitor — dev tasks (stdlib only; no external deps required)
.PHONY: test pyz clean run

test:
	cd tests && python3 -m unittest discover -s . -p "test_*.py"

pyz:
	bash scripts/build-pyz.sh

run:
	PYTHONPATH=src python3 -m halo_monitor $(ARGS)

clean:
	rm -rf dist build src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
