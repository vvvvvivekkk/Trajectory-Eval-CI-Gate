"""trajgate - trajectory evaluation with a CI gate.

Grade the PATH an agent took, not only the answer it landed on, then block
the pull request when the path gets worse.
"""

from .config import Config
from .gate import GateDecision, evaluate
from .graders import grade
from .schema import Golden, Step, StepKind, SuiteResult, TaskResult, Trajectory

__version__ = "0.1.0"
__all__ = [
    "Config", "Golden", "Step", "StepKind", "SuiteResult", "TaskResult",
    "Trajectory", "grade", "evaluate", "GateDecision",
]
