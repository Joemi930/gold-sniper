from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RESEARCH_BRANCHES = {"P1-opus"}


@dataclass(frozen=True)
class ResearchBranchDecision:
    allowed: bool
    reason: str
    branch: str
    research_shadow_only: bool


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def configured_research_branches() -> set[str]:
    raw = os.getenv("GOLD_SNIPER_RESEARCH_BRANCHES", "")
    branches = {item.strip() for item in raw.split(",") if item.strip()}
    return branches or set(DEFAULT_RESEARCH_BRANCHES)


def current_git_branch(repo_root: Path | None = None) -> str:
    for key in ("GOLD_SNIPER_BRANCH", "GITHUB_REF_NAME", "BRANCH_NAME", "GIT_BRANCH", "CI_COMMIT_BRANCH"):
        value = os.getenv(key)
        if value:
            return value.replace("refs/heads/", "").strip()

    root = repo_root or Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return "UNKNOWN"

    branch = (result.stdout or "").strip()
    return branch or "UNKNOWN"


def is_research_branch(branch: str | None = None) -> bool:
    effective_branch = branch or current_git_branch()
    return effective_branch in configured_research_branches()


def research_shadow_only_enabled(branch: str | None = None) -> bool:
    if _truthy(os.getenv("GOLD_SNIPER_RESEARCH_SHADOW_ONLY")):
        return True
    return is_research_branch(branch)


def evaluate_broker_write_request(
    *,
    run_mode: str,
    broker_writes_allowed: bool,
    branch: str | None = None,
) -> ResearchBranchDecision:
    del run_mode, broker_writes_allowed
    effective_branch = branch or current_git_branch()
    shadow_only = research_shadow_only_enabled(effective_branch)
    if shadow_only:
        return ResearchBranchDecision(
            allowed=False,
            reason="RESEARCH_BRANCH_SHADOW_ONLY",
            branch=effective_branch,
            research_shadow_only=True,
        )

    return ResearchBranchDecision(
        allowed=True,
        reason="NOT_RESEARCH_BRANCH",
        branch=effective_branch,
        research_shadow_only=False,
    )
