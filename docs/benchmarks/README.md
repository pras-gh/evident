# Benchmark reports

`tools/benchmark.py` writes one file here per run, named by run id.

Nothing is committed yet, because no run against Claude has happened. When one
does, commit the report — the point of a benchmark is comparison over time, and
a report that only exists on the machine that produced it cannot be compared
with anything.

Every report names the prompt version and model that produced it. A report
whose numbers moved without either of those changing is a signal about the
model; one where they did change is a signal about the change.

The raw responses behind each report stay in `extraction_runs`, keyed by the
same run id, so a report can be regenerated or replayed against a changed
schema without calling the API again.
