.PHONY: install test simulator orchestrator demo

install:
	pip install -e ".[dev]"

test:
	pytest -q

# Terminal 1: the replay simulator (the fake live feed).
simulator:
	python -m f1_commentator.simulator

# Terminal 2: the LLM orchestrator + audio streamer.
orchestrator:
	python -m f1_commentator.orchestrator

# Convenience: run both together (simulator in the background).
demo:
	python -m f1_commentator.simulator & \
	sleep 1 && python -m f1_commentator.orchestrator
