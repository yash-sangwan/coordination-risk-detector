# One entry point. Every number in the project comes from `make evaluate`.
.PHONY: evaluate verify chart clean-results

evaluate:
	python -m pipeline.evaluate

# Runs the numeric stages twice and fails loudly if results.json is not
# byte identical. Generation is skipped on the second pass because T7 already
# proves it byte identical.
verify:
	python -m pipeline.evaluate --verify

# Reuse the datasets already on disk.
reuse:
	python -m pipeline.evaluate --reuse

clean-results:
	rm -rf results
