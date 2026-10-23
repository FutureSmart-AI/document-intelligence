"""Batch Query Reports — worker-side pipeline.

The worker is stateless for reports: it fetches a run definition from the
Dashboard, executes each query against the Chat Service, posts results back,
optionally runs an analysis prompt, then closes out the run lifecycle.

See dashboard `docs/batch-query-reports-spec.md` for the contract.
"""
