"""A real LLM agent, so the eval has a real agent to grade.

The offline demo agent in demo_agent/ is scripted on purpose - it makes the
CI demo free, instant and identical every run. This package is the other
half: an ordinary tool-calling loop against Anthropic or OpenAI, writing the
same Trajectory the graders read.
"""

from .client import AnthropicClient, OpenAIClient, ScriptedClient, build_client
from .loop import run_task

__all__ = ["run_task", "build_client", "AnthropicClient", "OpenAIClient",
           "ScriptedClient"]
