# =============================================================================
# config.py: paths and the project's fixed settings
# =============================================================================
"""
Project-level configuration. Paths resolve from this file, so every tool works
from any working directory. The model block below is the one place a model is
named: no other source file may hold a model identifier (a test checks it).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
GENERATED_DIR = ROOT / "generated"

APP_TITLE = "Planning-to-Warehouse-to-LLM Pipeline"

# The most sessions (src/session.py) a process keeps open; one more closes the
# least recently used (part 2c).
MAX_SESSIONS = 20
REPO_URL = "https://github.com/cfpai0810/planning-to-llm-pipeline"

# -- The model ----------------------------------------------------------------
# Claude, via the Anthropic API (anthropic==0.116.0). Plain strings, not the
# SDK's typed model list, which does not name these models in 0.116.0.
# src/model_call.py is the only code that calls the model, and it reads this
# block; it never sends temperature, top_p or top_k, which current models
# reject. EFFORT is a starting value: Phase 4 sweeps it on the golden set.
# COMPARISON_MODEL is used only by the Phase 4 comparison run, with its own
# COMPARISON_EFFORT, equal to EFFORT for now. Whether that model accepts the
# effort setting is to be checked by the owner; if it does not, set
# COMPARISON_EFFORT to None and its requests carry no effort at all.

MODEL = "claude-sonnet-5-5"
COMPARISON_MODEL = "claude-haiku-5-5"
EFFORT = "medium"
COMPARISON_EFFORT = EFFORT
MAX_TOKENS = 16000
