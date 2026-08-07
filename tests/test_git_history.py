"""Tests for the git-history purge: the scripts, the report, the observer.

The scripts themselves are validated against real SWE-Bench Pro images (task-25
§5); these pin the properties that must not regress silently — the two defects
found in the upstream reference implementations especially, since both were
invisible to reading and only showed up when run.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, final, override

from etils import epath
import pytest

from swe_lab.datasets.instance import TaskInstance
from swe_lab.git_history import (
    build_purge_script,
    build_report_script,
    GitHistoryReport,
)
from swe_lab.sandbox import ExecResult, SandboxSpec
from swe_lab.sandbox.observers.git_history import (
    CLEAN_METRIC,
    FUTURE_BEFORE_METRIC,
    GitHistoryLeakError,
    GitHistoryPurgeObserver,
    INTEGRITY_ARTIFACT,
)
from swe_lab.sandbox.testing import FakeSandbox


@final
@dataclass(frozen=True)
class _PurgeInstance(TaskInstance[Any]):
  """A minimal instance that knows its fix commit — all the wiring reads."""

  instance_id: str = "acme__widget-1"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def solution_sha(self) -> str | None:
    return _FIX

  @override
  def prompt(self) -> str:
    return "solve it"

  @override
  def gold_patch(self) -> str | None:
    return None

  @override
  def unit_test_spec(self, **kwargs: Any) -> Any:
    raise NotImplementedError("this instance compiles no eval")


SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "basesha")
_FIX = "f" * 40


def _report_json(**overrides: Any) -> str:
  payload: dict[str, Any] = {
      "base_sha": "basesha",
      "refs": 0,
      "tags": 0,
      "heads": 0,
      "remote_refs": 0,
      "remotes": 0,
      "reflog": 0,
      "non_ancestor_commits": 0,
      "future_commits": 0,
      "base_reachable": True,
      "solution_reachable": False,
  }
  payload.update(overrides)
  return json.dumps(payload)


def _sandbox(tmp_path: Path, *outputs: str) -> FakeSandbox:
  return FakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      run_results=[ExecResult(0, out, "") for out in outputs],
      # These tests drive the purge itself, so the fake must not answer for it.
      git_report=None,
  )


# ─── the purge script ────────────────────────────────────────────────────────


def test_symbolic_refs_are_deleted_before_the_batch():
  # THE regression guard. `refs/remotes/origin/HEAD` is a symref to
  # `origin/main`; `update-ref --stdin` refuses to delete a symref and its
  # target in one transaction ("multiple updates for ...") and rolls the whole
  # batch back, purging NOTHING. The upstream reference implementation batches
  # them together and fails this way on any repo with an origin/HEAD.
  script = build_purge_script(workdir="/app")
  symref_at = script.index("git symbolic-ref --delete")
  batch_at = script.index("git update-ref --stdin")
  assert symref_at < batch_at


def test_a_failed_cd_aborts_before_anything_destructive():
  # THE dangerous one, and it is not hypothetical: an earlier build of this
  # script put `set -e` AFTER the `cd`, so a missing workdir left the shell in
  # whatever directory it started in and the purge deleted THAT repo's
  # branches, remotes and reflog. Every command after the cd is destructive and
  # none of them names a directory, so the cd is the only thing standing
  # between "purge the sandbox" and "purge the caller's checkout".
  for script in (
      build_purge_script(workdir="/app"),
      build_report_script(workdir="/app"),
  ):
    head = script.split("cd /app")[0]
    assert "set -eu" in head  # strict mode is in force BEFORE the cd
    assert "git" not in head  # and nothing has touched git yet
    assert "cd /app || {" in script  # checked explicitly on top of set -e
    # ... and the directory it landed in must be a repo root, or git would
    # happily walk up to an enclosing one.
    assert "--show-toplevel" in script


def test_tags_are_filtered_by_date_not_deleted_wholesale():
  # Past tags are legitimate research and some regression tasks need them;
  # SWE-bench Verified preserves them deliberately and we match it.
  script = build_purge_script(workdir="/app")
  assert "refs/tags" in script
  assert '-gt "$BASE_TS"' in script  # only tags NEWER than the base go
  assert "git tag -d" not in script  # never a blanket delete


def test_annotated_tags_are_dereferenced_to_their_commit():
  # An annotated tag is its own object pointing at the commit; comparing the
  # tag object's date would let tag-object indirection hide a future commit.
  assert '"${obj}^{}"' in build_purge_script(workdir="/app")


def test_the_purge_prunes_so_a_bare_sha_stops_resolving():
  # Deleting refs is not enough: an unreferenced object still answers to
  # `git show <sha>` and `git fsck --lost-found` until it is pruned.
  script = build_purge_script(workdir="/app")
  assert "git reflog expire --expire=now --all" in script
  assert "git gc --prune=now" in script
  # --aggressive costs ~2.5x the time for ~10% more space and blocks nothing
  # extra; deliberately omitted (task-25 §5).
  assert "--aggressive" not in script


def test_the_scripts_never_shell_out_to_date():
  # `date -d "<ts> + 1 second"` is GNU-only. Some instance images are Alpine
  # with busybox (upstream #75), where the reference assertion breaks. We
  # compare committer timestamps as integers instead.
  for script in (
      build_purge_script(workdir="/app"),
      build_report_script(workdir="/app", solution_sha=_FIX),
  ):
    assert "date -d" not in script


def test_the_workdir_is_quoted():
  assert "cd '/we ird'" in build_purge_script(workdir="/we ird")


# ─── the report script ───────────────────────────────────────────────────────


def test_the_report_script_always_exits_zero():
  # The script measures; the caller decides. A shell exiting non-zero mid-hook
  # would turn a policy decision into a stack trace.
  assert build_report_script(workdir="/app").rstrip().endswith("exit 0")


def test_solution_reachability_is_null_when_no_sha_is_known():
  assert "SOL=null" in build_report_script(workdir="/app")
  assert f"git cat-file -e {_FIX}" in build_report_script(
      workdir="/app", solution_sha=_FIX
  )


def test_the_solution_sha_is_quoted_into_the_script():
  script = build_report_script(workdir="/app", solution_sha="a b; rm -rf /")
  assert "'a b; rm -rf /'" in script


# ─── the report + assertions ─────────────────────────────────────────────────


def test_a_clean_report_has_no_violations():
  assert GitHistoryReport.from_json(_report_json()).violations() == ()


def test_a_reachable_solution_is_a_violation():
  report = GitHistoryReport.from_json(_report_json(solution_reachable=True))
  assert "solution commit is still reachable" in report.violations()[0]


def test_a_missing_base_commit_is_a_violation():
  # ADR-0001 diffs against base_commit and the eval script resets to it, so a
  # purge that loses it breaks extraction and grading, not just history.
  report = GitHistoryReport.from_json(_report_json(base_reachable=False))
  assert "unreachable" in report.violations()[0]


def test_future_commits_are_a_violation_even_with_no_solution_sha():
  # The load-bearing assertion: it catches leaks whose sha we never knew.
  report = GitHistoryReport.from_json(
      _report_json(future_commits=12, solution_reachable=None)
  )
  assert report.violations() == (
      "12 reachable commit(s) postdate the base commit",
  )


def test_non_ancestor_commits_are_context_not_a_violation():
  # A correct purge that keeps past tags legitimately leaves thousands of
  # commits outside HEAD's ancestry (ansible: 9630, all past-dated). Asserting
  # on ancestry would fail every clean run; only the DATE separates a leak.
  report = GitHistoryReport.from_json(
      _report_json(non_ancestor_commits=9630, future_commits=0)
  )
  assert report.violations() == ()


def test_the_report_survives_noise_around_the_json():
  raw = f"warning: something\n{_report_json()}\n"
  assert GitHistoryReport.from_json(raw).base_sha == "basesha"


def test_a_report_without_json_is_an_error():
  with pytest.raises(ValueError, match="no JSON object"):
    _ = GitHistoryReport.from_json("bash: git: not found")


# ─── the observer ────────────────────────────────────────────────────────────


def test_the_purge_runs_before_the_agent_and_reports_both_sides(
    tmp_path: Path,
):
  sb = _sandbox(
      tmp_path,
      _report_json(refs=237, future_commits=3426, solution_reachable=True),
      "",  # the purge itself
      _report_json(refs=68, non_ancestor_commits=18, future_commits=0),
  )
  observer = GitHistoryPurgeObserver(solution_sha=_FIX)
  observer.after_create(sb)
  # after_create, so this happened while the agent had not yet started.
  assert sb.scripts == ["git_report.sh", "git_purge.sh", "git_report.sh"]
  assert observer.before is not None and observer.before.future_commits == 3426
  assert observer.after is not None and observer.after.future_commits == 0

  contribution = observer.before_destroy(sb)
  assert contribution is not None
  payload = json.loads(contribution.inline_artifacts[INTEGRITY_ARTIFACT])
  assert payload["before"]["future_commits"] == 3426
  assert payload["after"]["future_commits"] == 0
  assert payload["violations"] == []
  assert contribution.metrics[CLEAN_METRIC] == 1.0
  assert contribution.metrics[FUTURE_BEFORE_METRIC] == 3426.0


def test_a_leak_that_survives_the_purge_stops_the_run(tmp_path: Path):
  # The whole point: refuse to run the agent against a contaminated repo,
  # rather than produce a number that looks real and is not.
  sb = _sandbox(
      tmp_path,
      _report_json(solution_reachable=True),
      "",
      _report_json(solution_reachable=True, future_commits=3426),
  )
  with pytest.raises(GitHistoryLeakError, match="still reachable"):
    GitHistoryPurgeObserver(solution_sha=_FIX).after_create(sb)


def test_a_purge_that_fails_to_run_is_itself_a_leak(tmp_path: Path):
  # This is how the upstream symref bug presents: update-ref aborts, the
  # transaction rolls back, and nothing is purged. A non-zero exit must never
  # be shrugged off as a best-effort setup step.
  sb = FakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      run_results=[
          ExecResult(0, _report_json(solution_reachable=True), ""),
          ExecResult(128, "fatal: multiple updates for 'refs/...'", ""),
      ],
      git_report=None,
  )
  with pytest.raises(GitHistoryLeakError, match="purge failed"):
    GitHistoryPurgeObserver(solution_sha=_FIX).after_create(sb)


def test_an_unverifiable_repo_is_treated_as_contaminated(tmp_path: Path):
  sb = _sandbox(tmp_path, "bash: git: command not found")
  with pytest.raises(GitHistoryLeakError, match="could not verify"):
    GitHistoryPurgeObserver().after_create(sb)


def test_report_only_mode_measures_without_touching_the_repo(tmp_path: Path):
  # What the audit workflow uses to characterize an untouched image.
  sb = _sandbox(tmp_path, _report_json(refs=237, future_commits=3426))
  observer = GitHistoryPurgeObserver(purge=False)
  with pytest.raises(GitHistoryLeakError, match="postdate the base"):
    observer.after_create(sb)
  assert sb.scripts == ["git_report.sh"]  # no purge script was ever run


def test_nothing_is_contributed_when_the_hook_never_ran(tmp_path: Path):
  # A sandbox that failed to come up never reaches after_create; the observer
  # must not claim a required artifact it does not have.
  assert GitHistoryPurgeObserver().before_destroy(_sandbox(tmp_path)) is None


# ─── wiring ──────────────────────────────────────────────────────────────────


def test_the_coding_agent_task_purges_first_and_by_default(tmp_path: Path):
  # Contributed by the task so no caller can forget it, and FIRST so the agent
  # and every later hook see an already-clean repo (ADR-0010 §3b).
  from swe_lab.harnesses.claude_code import ClaudeCodeHarness
  from swe_lab.rollout import CodingAgentTask

  del tmp_path
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  observers = task.observers(_PurgeInstance())
  assert isinstance(observers[0], GitHistoryPurgeObserver)
  assert observers[0].solution_sha == _FIX


def test_the_purge_can_be_turned_off_deliberately():
  from swe_lab.harnesses.claude_code import ClaudeCodeHarness
  from swe_lab.rollout import CodingAgentTask

  task = CodingAgentTask(harness=ClaudeCodeHarness(), purge_git_history=False)
  observers = task.observers(_PurgeInstance())
  assert not any(isinstance(o, GitHistoryPurgeObserver) for o in observers)


def test_the_audit_task_is_the_same_observer_with_no_agent():
  # An audit pass must mean the rollout's purge is the same code, not a
  # lookalike — so it contributes the identical observer and nothing else.
  from swe_lab.integrity import GitIntegrityAuditTask

  observers = GitIntegrityAuditTask().observers(_PurgeInstance())
  assert len(observers) == 1
  purging = observers[0]
  assert isinstance(purging, GitHistoryPurgeObserver)
  assert purging.purge is True
  reporting = GitIntegrityAuditTask(purge=False).observers(_PurgeInstance())[0]
  assert isinstance(reporting, GitHistoryPurgeObserver)
  assert reporting.purge is False


def test_the_audit_workflow_is_registered_and_offline():
  # Nothing in the audit needs egress, and constraining it exactly as the
  # rollout should be is what keeps the audit honest.
  import swe_lab.workflow.definitions as definitions
  from swe_lab.workflow.registry import workflow_definition

  del definitions  # imported for its registration side effect
  entries = workflow_definition("git_integrity_audit")
  assert len(entries) == 1
  assert entries[0].sandbox.network is False


def test_swebench_pro_reads_the_fix_sha_out_of_the_instance_id():
  # It is NOT base_commit (that is the commit before the fix, a separate
  # column); matched on the 40-hex shape because repo names contain hyphens
  # and the trailing -v suffix is sometimes "nan".
  from swe_lab.datasets.swebench_pro.record import _FIX_SHA_RE

  sha = "0" * 40
  suffixed = _FIX_SHA_RE.search(f"instance_NodeBB__NodeBB-{sha}-vnan")
  bare = _FIX_SHA_RE.search(f"instance_a__b-c-d-{sha}")
  assert suffixed is not None and suffixed.group(1) == sha
  assert bare is not None and bare.group(1) == sha
  assert _FIX_SHA_RE.search("instance_a__b-nosha") is None


def test_a_leak_is_recorded_by_the_engine_rather_than_escaping():
  # THE near-miss. `Task.execute` catches SandboxError and lets the caller gate
  # on run.status; anything else propagates out of the workflow and takes the
  # whole record with it. An unrecorded contamination is indistinguishable
  # from a run that never happened, which is the one outcome worse than the
  # leak itself (ADR-0009 / task-25 §8).
  from swe_lab.sandbox import SandboxError

  assert issubclass(GitHistoryLeakError, SandboxError)


def test_an_integrity_failure_is_never_retried():
  # Deterministic, not flaky: the same image purges the same way every time, so
  # a retry buys the same verdict a container later — and reads as flakiness in
  # the record when it is a property of the image.
  from swe_lab.harnesses.claude_code import ClaudeCodeHarness
  from swe_lab.integrity import GitIntegrityAuditTask
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.sandbox import RunResult, RunStatus
  from swe_lab.workflow import AttemptResult

  leaked = AttemptResult(
      run=RunResult(
          label="x",
          status=RunStatus.SETUP_ERROR,
          artifacts={},
          metrics={},
          error=GitHistoryLeakError("still reachable"),
      ),
      exec_result=None,
      output_schema=(),
      observers=(),
  )
  assert (
      CodingAgentTask(harness=ClaudeCodeHarness()).should_retry(leaked) is False
  )
  assert GitIntegrityAuditTask().should_retry(leaked) is False


def test_the_evaluation_sandbox_is_never_purged():
  # The eval needs its refs for the golden-test restore step, and no agent runs
  # in it — so the purge attaches to the solving task alone (ADR-0010 §3b).
  from swe_lab.datasets.loader import load_dataset
  import swe_lab.workflow.definitions as definitions
  from swe_lab.workflow.registry import workflow_definition

  del definitions  # imported for its registration side effect
  # A real instance: the grading task compiles an eval spec from it, which a
  # stub cannot supply.
  record = next(iter(load_dataset("swebench_pro")))
  assert isinstance(record, TaskInstance)
  for name in ("unit_test", "gold_unit_test"):
    for entry in workflow_definition(name):
      kinds = [type(o).__name__ for o in entry.task.observers(record)]
      assert "GitHistoryPurgeObserver" not in kinds, f"{name}/{entry.key}"
