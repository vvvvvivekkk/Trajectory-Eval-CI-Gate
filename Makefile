# Convenience targets. `make demo` is the one to try first.
PY ?= python3

.PHONY: install test run red green baseline demo trace clean

install:        ## install dependencies
	$(PY) -m pip install -r requirements-dev.txt

test:           ## unit tests for the graders and the gate
	$(PY) -m pytest -q

run:            ## grade the current agent and apply the gate
	$(PY) -m trajgate run --agent $$(tr -d '[:space:]' < AGENT_VERSION)

green:          ## the good agent: gate passes, exit 0
	$(PY) -m trajgate run --agent v1.0-good

red:            ## the regressed agent: gate blocks, exit 1
	-$(PY) -m trajgate run --agent v1.1-regressed

flaky:          ## a deliberately unstable agent, run 5 times per task
	-$(PY) -m trajgate run --agent v1.3-flaky --repeats 5 --no-gate

baseline:       ## record the good agent as the baseline to be judged against
	$(PY) -m trajgate run --agent v1.0-good --no-gate
	$(PY) -m trajgate baseline --report out/report.json --out baselines/baseline.json

trace:          ## dump the raw step-by-step paths to out/traces.jsonl
	$(PY) -m trajgate trace --agent v1.1-regressed --out out/traces.jsonl

demo: baseline  ## full story: green, then red, then the fix
	@echo "\n================ 1. the agent as it is today ================"
	@$(PY) -m trajgate run --agent v1.0-good; echo "exit code: $$?"
	@echo "\n==== 2. someone 'improves the prompt' and ships v1.1 ========"
	@-$(PY) -m trajgate run --agent v1.1-regressed; echo "exit code: $$? <- merge blocked"
	@echo "\n================ 3. the regression is fixed ================="
	@$(PY) -m trajgate run --agent v1.2-fixed; echo "exit code: $$?"

clean:
	rm -rf out .pytest_cache **/__pycache__
