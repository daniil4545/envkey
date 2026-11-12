RUFF = uvx ruff@0.14.0

.PHONY: lint
lint:
	$(RUFF) check .
# The format check is off: ruff format would rewrite bin/envkey, hooks/guard.py and tests/test_envkey.py.
