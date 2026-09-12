"""The research orchestrator.

Four durable steps over `run_steps`, wired in `pipeline.py`: interpret, plan,
research, synthesize. The research step is the loop — retrieve, extract, assess
sufficiency, repeat until the gate says stop.

Stages 5, 6, 8, 9 and 11 of implementation plan §5.1 — normalise, dedupe and
tier, conflict, confidence, visualize — are Phase 2 and 3 work. They slot in
between the existing steps without moving those boundaries, which is why the
steps are grouped rather than one per stage.

The Phase 0 walking skeleton lived here and is gone: it was two no-op stages
that proved the seams, and Phase 1 replaced both.
"""
