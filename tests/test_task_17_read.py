"""The pre-registered read for task 17 must match the table beside it.

The read is written before the data exists so that nobody invents it
afterwards, which only helps if the snippet and its table cannot drift apart —
and if the file it names is the file the pipeline actually writes.

The first version of that check could not have caught a wrong name: its
fixtures were built from the snippet's own glob, so it confirmed the snippet
agreed with itself. The artifact name is therefore checked against the shipped
harness here, which is a source the snippet did not author.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tempfile

import pytest

from swe_lab.harnesses.claude_code.harness import ClaudeCodeHarness
from swe_lab.sandbox.result import qualified_name

_README = (
    Path(__file__).resolve().parents[1] / "docs/trace-synthesis/plans/README.md"
)
_HEADING = "### Task 17's first input"


def _snippet() -> str:
  """Return the Python fence under task 17's heading, as committed.

  Returns:
    The snippet's source, exactly as a reader would paste it.
  """
  section = _README.read_text().split(_HEADING)[1]
  return section.split("```python\n")[1].split("```")[0]


def _run(directory: Path) -> tuple[int, str, str]:
  """Run the committed snippet against a directory.

  The snippet is whatever the Markdown says today, so it is run on a leash: a
  fence that comes to loop or print without end would otherwise hang the suite
  and fill memory with its own output.

  Args:
    directory: The path handed to the read.

  Returns:
    Its exit status, stdout and stderr.
  """
  with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
    status = subprocess.run(
        [sys.executable, "-c", _snippet(), str(directory)],
        stdout=out,
        stderr=err,
        timeout=30,
        check=False,
    ).returncode
    _ = out.seek(0)
    _ = err.seek(0)
    return status, out.read(), err.read()


def test_the_read_names_an_artifact_the_supervised_harness_collects():
  """The globbed name is the collected artifact, not the workspace copy.

  Both files exist after a run and both are findable, so aiming the read at
  the container-side name would still print a plausible line — off the scratch
  copy a re-run deletes. Only the harness can settle which name is which.
  """
  globbed = re.search(r'rglob\("([^"]+)"\)', _snippet())
  assert globbed is not None
  harness = ClaudeCodeHarness(capture="proxy", correction_channel=True)
  collected = {
      qualified_name(harness.name, role) for role in harness.native_outputs()
  }
  assert globbed.group(1) in collected


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (None, "0 file(s), 0 events, 0 result"),
        ([], "1 file(s), 0 events, 0 result"),
        (['{"type": "assistant"}'], "1 file(s), 1 events, 0 result"),
        (
            ['{"type": "assistant"}', '{"type": "result"}'],
            "1 file(s), 2 events, 1 result",
        ),
    ],
    ids=["no file", "empty file", "events, no result", "result present"],
)
def test_each_reading_in_the_table_is_what_the_snippet_prints(
    tmp_path: Path, lines: list[str] | None, expected: str
):
  """Every row of the table is the snippet's actual output for that case.

  Args:
    tmp_path: The run directory to read.
    lines: The stream's contents, or ``None`` for no stream at all.
    expected: The reading the table promises.
  """
  attempt = tmp_path / "a0"
  attempt.mkdir()
  if lines is not None:
    stream = attempt / "claude_code.event_stream.jsonl"
    _ = stream.write_text("".join(line + "\n" for line in lines))
  status, out, err = _run(tmp_path)
  assert status == 0, err
  assert out.strip() == expected


def test_more_than_one_attempt_refuses_instead_of_merging_them(tmp_path: Path):
  """A path spanning two attempts yields no reading at all.

  The counts are summed across whatever the glob finds, so two attempts would
  otherwise print one line that describes neither of them.

  Args:
    tmp_path: The run directory to read.
  """
  for attempt in ("a0", "a1"):
    directory = tmp_path / attempt
    directory.mkdir()
    _ = (directory / "claude_code.event_stream.jsonl").write_text(
        '{"type": "result"}\n'
    )
  status, out, err = _run(tmp_path)
  assert status != 0
  assert out.strip() == ""
  assert "2 file(s)" in err
