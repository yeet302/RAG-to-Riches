"""Code identity defaults on the canonical specialist JSON contract."""
from typing import Literal
from agent_contracts import AgentRequest, Evidence
from agent_contracts import AgentFinding as SharedAgentFinding


class AgentFinding(SharedAgentFinding):
    agent: Literal['code'] = 'code'
