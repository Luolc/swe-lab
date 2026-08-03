# Task 18 — Sandbox observability + interface reshape (prompt, instance mounts)

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Implements the additive/breaking preliminaries of
> [ADR-0007](../../decisions/ADR-0007-task-and-workflow-layer.md) (§2 instance
> mounts, §3 observer sources, §8 prompt-as-argument), so Task 19 builds the
> `Task` layer on the final shape of every interface it composes.

---

## 1. Purpose & scope

Three interface changes that ADR-0007 requires and that do **not** depend on the
`Task` layer existing. They are batched because two of them are breaking for a
downstream `Harness`/backend implementation, and downstream should upgrade once,
not twice.

### In scope

1. `Sandbox.observers()` — backends contribute their own observers; the host
   backend ships a runtime-metrics observer (the `de49d486` diagnosis gap).
2. `Harness.run(prompt=...)` — the prompt becomes an argument; `PROMPT_NAME`
   is retired.
3. `TaskInstance.mounts()` — the instance becomes an ordinary mount source.

### Out of scope

- The `Task` class itself, output declarations, retry/resume (Tasks 19–20).
- A ghjob metrics observer (the seam is open; the backend can add one later —
  its runner has no live `docker` CLI to poll).
- Wiring instance `mounts()` into the *existing* compositions beyond the eval
  path's spec compilation (Task 19 rewires everything through `Task`).

---

## 2. Interface definitions

### 2.1 `Sandbox.observers()` (additive)

```python
class Sandbox(SandboxFs, ABC):

  def observers(self) -> Sequence[SandboxObserver]:
    """Return this backend's own observers for the coming run.

    The backend is the only party that can measure its own runtime — OOM
    kills, peak memory, setup time — so it contributes observers exactly like
    the runner and the task do (ADR-0007 §3). Called once per run, before
    ``up``; a fresh list each call, since stateful observers are single-run.

    Default: none. A backend without runtime metrics stays a one-liner.
    """
    return []
```

- `SandboxManager` composes them **first** (they measure the whole run):

```python
# manager-side composition (in Task 19 this moves into Task; until then the
# two existing compositions prepend them explicitly):
observers = [*sandbox.observers(), *composition_observers]
```

### 2.2 `HostMetricsObserver` (new, `sandbox/backends/host.py`)

```python
@dataclass
class HostMetricsObserver(SandboxObserver):
  """Collect container runtime metrics while the sandbox is still live.

  Single-run. Reads through the backend handle, not through ``SandboxFs`` —
  these numbers live in Docker, not in the workspace, which is exactly why
  only the backend can contribute this observer.

  Metrics (all namespaced ``sandbox.``):
    sandbox.setup_seconds       wall clock of ``up`` (image pull excluded)
    sandbox.peak_memory_bytes   ``docker stats`` peak observed at teardown,
                                or the cgroup peak when readable
    sandbox.oom_killed          1.0 if ``State.OOMKilled`` on inspect
    sandbox.exit_code           the container's own exit code at inspect
  """

  backend: DockerHostSandbox   # set by the backend when it builds the observer

  @override
  def after_create(self, sb: SandboxFs) -> None: ...   # stamp setup end
  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    # docker inspect + stats while the container still exists;
    # return Contribution(metrics={...}) — no artifacts.
    ...
```

Implementation notes:

- **Read at `before_destroy`, not per-poll.** `docker inspect`'s
  `State.OOMKilled` and the cgroup's `memory.peak` are cumulative, so one read
  at teardown is enough; no background thread.
- `memory.peak` tier order: cgroup v2 file → `docker stats --no-stream` fallback
  → metric omitted (never `0.0`, absent means unmeasured).
- Failure to read any metric is **logged and skipped**, never raised — metrics
  must not be able to fail a graded run (`on_error` still fires normally).

### 2.3 `Harness.run` gains `prompt`; `PROMPT_NAME` retired (breaking)

```python
class Harness(ConversationProducer, ABC):

  @abstractmethod
  def run(
      self,
      sb: SandboxFs,
      *,
      prompt: str,                              # ← NEW, required, keyword-only
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run the agent against ``prompt`` in the live sandbox.

    Where the prompt lands — a file, argv, stdin — is the harness's business,
    decided here at run time (``sb.write`` exists for the file case). The
    dataset owns the prompt's *content*; the harness owns everything about
    where it goes (ADR-0007 §8).
    """
```

- `harnesses/base.py` **deletes** `PROMPT_NAME`. `claude_code` writes the prompt
  itself at the top of `run()`:

```python
# claude_code/harness.py — inside run(), replacing the old composition-staged
# prompt.txt; same in-sandbox path as before, now the harness's own choice:
sb.write(_PROMPT_FILENAME, prompt.encode())   # _PROMPT_FILENAME = "prompt.txt"
```

- `run_rollout(prompt=...)` keeps its signature but stops staging the prompt as
  a mount; it forwards to `harness.run(sb, prompt=prompt, ...)`.
- The `prompt.txt` row in `docs/horizontal/workspace-layout.md` moves from
  "dataset/composition (mount)" to the harness's own files.

### 2.4 `TaskInstance.mounts()` (additive, default empty)

```python
class TaskInstance[V: Verdict](ABC):

  def mounts(self) -> Mounts:
    """Return the dataset's own material to stage for a run of this instance.

    The instance is one of the four mount sources (ADR-0007 §2) and decides
    for itself whether it stages anything. Default: nothing — solving-only
    datasets, or one whose material is all baked into the image.

    Names are workspace-relative, like every other contributor's;
    ``merge_mounts`` already refuses duplicate targets across sources.
    """
    return {}
```

- `SweBenchProInstance.mounts()` is **not** implemented in this task. Its
  material (run script / parser / expectation) is compiled per-patch inside
  `unit_test_spec`, and splitting that compilation is Task 19's business — done
  here it would have two sources of truth for one mount set. The interface
  lands now so downstream sees the final `TaskInstance` once.

---

## 3. Steps (each green before the next)

1. `Sandbox.observers()` default + manager-side prepend in both compositions;
   fake-backend test that a backend observer's metrics land in `RunResult`.
2. `HostMetricsObserver` + `DockerHostSandbox.observers()` returning it.
   Live-marked test (`docker` required): run a trivial container, assert
   `sandbox.setup_seconds > 0` and `sandbox.oom_killed == 0.0`; a second test
   runs a memory hog under `--memory` and asserts `sandbox.oom_killed == 1.0`.
3. `Harness.run(prompt=...)` + `PROMPT_NAME` deletion + `claude_code` update +
   `run_rollout` forwarding; update both CLI fakes and workspace-layout doc.
4. `TaskInstance.mounts()` default + docs.
5. Full bar, one PR, **one breaking-changes section listing 2.3 (and noting
   2.1/2.4 are additive)**.

## 4. Definition of done

- A run through the host backend records `sandbox.*` metrics in `RunResult`
  and in a persisted record, with no change to any verdict.
- `grep -rn PROMPT_NAME src/` is empty; rollout stages no prompt mount; the
  claude_code harness writes its own prompt file and a live rollout still
  produces a patch (manual CP, same bar as task 07's).
- Downstream-visible deltas called out in the PR body.

## 5. Risks

- **`docker stats` peak is sampled, not true peak** — the cgroup file is the
  real source; stats is the degraded tier and the metric's docstring says so.
- **A backend observer that throws** would fail runs it should only decorate:
  every hook in `HostMetricsObserver` catches and logs (asserted by a test that
  injects a failing `docker` CLI).
- The `Harness.run` break is the whole reason this is one batch; landing it
  after Task 19 would force downstream through two upgrades.
