"""Process-wide LLM throttle + timeout, ported from fs-agent-chat/chat_utils/llm_limiter.py.

Every chat model built by chat/llm_factory.py::get_llm passes through
apply_llm_limits() so all outbound LLM traffic in this process shares ONE
token bucket — the same mechanism the Orchestrator uses to stay under a
provider's requests-per-minute limit instead of hitting 429s under burst load.

Tuning (env, see utils/setup_env.py):
  LLM_MAX_RPS          requests/sec admitted process-wide.
                       Default 8 ~= OpenAI tier-1's 500 RPM (500/60).
  LLM_MAX_BURST        token-bucket size (defaults to LLM_MAX_RPS).
  LLM_REQUEST_TIMEOUT  per-call timeout (s) so a stuck/throttled call fails
                       fast instead of hanging the request.

The bucket is PER PROCESS. With N uvicorn workers the effective ceiling is
N * LLM_MAX_RPS — divide accordingly when scaling out.
"""

from langchain_core.rate_limiters import InMemoryRateLimiter

from utils.logger import logger
import utils.setup_env as setup_env

LLM_MAX_RPS = setup_env.LLM_MAX_RPS
LLM_MAX_BURST = setup_env.LLM_MAX_BURST
LLM_REQUEST_TIMEOUT = setup_env.LLM_REQUEST_TIMEOUT

# ONE shared bucket for the whole process — attached to every chat model.
LLM_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=LLM_MAX_RPS,
    check_every_n_seconds=0.1,
    max_bucket_size=LLM_MAX_BURST,
)

# Timeout field names differ across provider integrations; set whichever exists.
_TIMEOUT_FIELDS = ("request_timeout", "timeout", "default_request_timeout")

logger.info(
    f"[llm_limiter] initialized: rps={LLM_MAX_RPS} burst={LLM_MAX_BURST} timeout={LLM_REQUEST_TIMEOUT}s"
)


def apply_llm_limits(llm):
    """Attach the shared rate limiter (and a best-effort timeout) to a chat model.

    BaseChatModel reads self.rate_limiter at invoke time, so setting it
    post-construction is reliable across all providers:

        llm = apply_llm_limits(ChatOpenAI(...))
    """
    try:
        llm.rate_limiter = LLM_RATE_LIMITER
    except Exception as e:  # never let throttling wiring break a request
        logger.warning(f"[llm_limiter] could not attach rate_limiter: {e}")
    for field in _TIMEOUT_FIELDS:
        if hasattr(llm, field):
            try:
                setattr(llm, field, LLM_REQUEST_TIMEOUT)
            except Exception:
                pass
            break
    return llm
