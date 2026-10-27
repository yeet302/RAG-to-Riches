"""Compatibility imports for the shared v1 specialist contract."""
# Support the documented launch command from this service directory.
from pathlib import Path
import sys

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_contracts import AgentName, SourceType, AgentRequest, AgentFinding, Evidence
