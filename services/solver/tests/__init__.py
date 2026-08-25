"""Tests for the §5 solver pipeline and the §5.7 partial re-solve.

Everything in here runs without OR-Tools: the CP-SAT stage bodies are injected as
pure-Python fakes through :class:`services.solver.pipeline.StageSet`, which is
exactly the seam the constraint "your CP-SAT code cannot be executed on this
machine" exists to justify. What IS proven here: orchestration order, the §15
staged events, §5.6 gates + relax-once, §5.5 diversity through the driver,
checkpoint resume, §5.7 obstacle masking / locked-wall dedupe / id preservation,
and diff-matching against the real ``garh_model`` Jaccard primitive.
"""
