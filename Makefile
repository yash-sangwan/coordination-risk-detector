# One entry point. Every number in the project comes from `make evaluate`.
.PHONY: evaluate verify reuse memory-profile from-logs check-docs render-docs clean-results

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

# Bounded-state demonstration, split out of the main run on 2026-08-30. It
# re-streams the file five times to show peak memory tracks the event rate and
# not the stream length, which does not change between runs, so it is a one-off
# rather than part of every verification. Writes results/memory_profile.json.
# Re-run one stage after a failure, then rebuild: 
#   python -m pipeline.evaluate --stage 06_streaming
#   python -m pipeline.evaluate --from-logs
memory-profile:
	python -m pipeline.evaluate --memory-profile

# Rebuild results.json from archived logs after a parser or schema change,
# without re-running any stage.
from-logs:
	python -m pipeline.evaluate --from-logs

# Fail if a generated document no longer matches its template, which means it
# was edited by hand or an earlier run left it stale. Either way the next render
# discards the difference.
check-docs:
	python -m pipeline.cite --check

render-docs:
	python -m pipeline.cite --render-all

clean-results:
	rm -rf results
