# =============================================================================
# config.py: paths and the project's fixed settings
# =============================================================================
"""
Project-level configuration. Paths resolve from this file, so every tool works
from any working directory. Phase 0b adds the model block here (MODEL, EFFORT,
MAX_TOKENS and COMPARISON_MODEL), the one place a model is named.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
GENERATED_DIR = ROOT / "generated"

APP_TITLE = "Planning-to-Warehouse-to-LLM Pipeline"
REPO_URL = "https://github.com/cfpai0810/planning-to-llm-pipeline"
