"""The rollout solve prompt: instruct the in-container agent to fix the repo.

Deliberately dataset-agnostic — it takes plain strings (a problem statement, and
optional requirements / interface hints), not a dataset record, so the runner
stays general. Unlike the annotation prompt, the agent is **not** shown the gold
patch: rollout is the agent genuinely solving the task. The harness-agnostic
contract is that the fix is whatever the working tree gains — captured later as
``git diff`` — so the prompt only needs to get the agent to edit the repo.
"""

from __future__ import annotations


def build_solve_prompt(
    problem_statement: str,
    *,
    requirements: str = "",
    interface: str = "",
) -> str:
  """Compose the instruction handed to the headless agent in the container."""
  sections = [
      "You are working inside a checked-out software repository. Your task is"
      " to resolve the following issue by editing the code in this repository"
      " directly.",
      "",
      "## Problem statement",
      "",
      problem_statement.strip(),
  ]
  if requirements.strip():
    sections += [
        "",
        "## Requirements the fix must satisfy",
        "",
        requirements.strip(),
    ]
  if interface.strip():
    sections += [
        "",
        "## Interface / API the fix must conform to",
        "",
        interface.strip(),
    ]
  sections += [
      "",
      "## How to work",
      "",
      "- Explore the repository to understand the relevant code first.",
      "- Make the necessary source changes directly in the working tree.",
      "- Aim for a correct, minimal fix that a maintainer would accept; do not"
      " modify unrelated code, and do not edit tests to make them pass.",
      "- You do not need to commit — your edits to the working tree are what"
      " gets captured.",
      "- When you are confident the issue is resolved, finish.",
  ]
  return "\n".join(sections) + "\n"
