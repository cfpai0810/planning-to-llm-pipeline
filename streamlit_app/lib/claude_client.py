# =============================================================================
# lib/claude_client.py: the client seam, one client per session's own key
# =============================================================================
# The web app cannot build its client at start-up: a visitor pastes a key
# after the app loads, and two visitors have two keys. So the client is built
# on demand from the pasted key, passed explicitly, never from the server's
# environment and never kept as a module global. src/model_call.py is the
# only code that calls it.
# =============================================================================

import anthropic


class NoKeyError(RuntimeError):
    """A client was asked for with no key."""


def build_client(api_key: str) -> anthropic.Anthropic:
    """A client for a pasted key. The key is passed to the client and kept nowhere else."""
    if not api_key or not api_key.strip():
        raise NoKeyError("No API key provided.")
    return anthropic.Anthropic(api_key=api_key.strip())


def looks_like_anthropic_key(api_key: str) -> bool:
    """A cheap check for an obvious paste mistake. Only a real call proves a key works."""
    key = (api_key or "").strip()
    return key.startswith("sk-ant-") and len(key) > 20
