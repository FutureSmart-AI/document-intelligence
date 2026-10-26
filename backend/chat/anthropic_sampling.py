"""Which Anthropic models still accept the ``temperature`` parameter.

Anthropic removed ``temperature`` from its newer models: sending it returns
HTTP 400 ``{"type": "invalid_request_error", "message": "`temperature` is
deprecated for this model."}``. This is not a warning — the whole request
fails, so a caller that always sends ``temperature=0`` for determinism cannot
use those models at all. That is exactly how the extraction judge silently
recorded zero scores: it asked for ``claude-sonnet-5`` at ``temperature=0``,
every call 400'd, and the judge's own error handler turned that into
"no opinion" rather than a visible failure.

The boundary was established empirically against the live Messages API on
2026-08-27 with one-token probe requests, because ``GET /v1/models`` does not
expose a sampling capability flag (its ``capabilities`` object covers batch,
citations, code_execution, context_management, effort, image_input, pdf_input,
structured_outputs and thinking — nothing about temperature):

    accepts  claude-opus-4-6, claude-sonnet-4-6, claude-opus-4-5-*,
             claude-sonnet-4-5-*, claude-haiku-4-5-*
    rejects  claude-opus-4-7, claude-opus-4-8, claude-opus-5,
             claude-sonnet-5, claude-fable-5

So the cutoff sits between the 4.6 and 4.7 generations. Note the two
generations either side of it use the same knob differently: newer models
steer determinism with ``effort``/adaptive thinking instead.

An UNKNOWN model is treated as NOT accepting temperature — the fail-safe
direction. Omitting the parameter from a model that would have accepted it
costs some determinism; sending it to a model that rejects it costs the entire
request. Newer Anthropic models follow the newer convention, so a model this
list has not seen is far more likely to be on the rejecting side.
"""

from typing import Optional

# Model-ID prefixes known to accept ``temperature``. Matched as prefixes so
# dated snapshots (claude-haiku-4-5-20251001) resolve without a new entry.
_TEMPERATURE_OK_PREFIXES = (
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-3",  # every claude-3.x generation predates the removal
)


def anthropic_accepts_temperature(model: Optional[str]) -> bool:
    """True when ``temperature`` may be sent to this Anthropic model.

    Unknown models return False — see the module docstring for why that is the
    safe default rather than the cautious-looking one.
    """
    if not model:
        return False
    m = model.lower().strip()
    return m.startswith(_TEMPERATURE_OK_PREFIXES)
