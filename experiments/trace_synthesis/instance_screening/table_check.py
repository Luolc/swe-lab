#!/usr/bin/env python3
"""Verify REPORT.md's runnability table against the screening data.

Split out of ``verdicts.py`` so it can be tested without the dataset. The check
it replaced asserted that a partition of the rows summed to the number of rows —
true by construction, reading nothing from the report, and green even with a
family deleted from the table. This one reads the table.
"""

from __future__ import annotations

from collections import Counter
import re

# The name each table row uses -> the repo it means. Explicit, because substring
# matching would let a renamed or duplicated family match by accident, and drift
# in these names is the thing being caught.
FAMILY_REPO = {
    "qutebrowser": "qutebrowser/qutebrowser",
    "openlibrary": "internetarchive/openlibrary",
    "vuls": "future-architect/vuls",
    "navidrome": "navidrome/navidrome",
    "teleport": "gravitational/teleport",
    "NodeBB": "NodeBB/NodeBB",
    "ansible": "ansible/ansible",
    "protonmail/webclients": "protonmail/webclients",
    "element-web": "element-hq/element-web",
    "tutanota": "tutao/tutanota",
    "flipt": "flipt-io/flipt",
}

_HEADER = "| family | instances in #261 | status |"


def check_runnability_table(report: str, by_repo: Counter[str], total: int) -> str:
  """Check the report's runnability table against the per-repo instance counts.

  Args:
    report: the full text of ``REPORT.md``.
    by_repo: how many instances each repository contributes.
    total: how many instances there are altogether.

  Returns:
    A one-line summary of what was verified.

  Raises:
    AssertionError: if a row's count disagrees with the data, a row names a
      family that is not known, a repository is named by more or fewer than
      exactly one row, or the rows do not account for every instance.
  """
  table = report.split(_HEADER)[1].split("\n\n")[0]
  claimed, covered = 0, []
  for line in table.splitlines():
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) != 3 or not cells[1].isdigit():
      continue
    names = re.findall(r"`([^`]+)`", cells[0])
    unknown = [name for name in names if name not in FAMILY_REPO]
    assert not unknown, f"REPORT runnability row {cells[0]}: unknown family name {unknown}"
    repos = [FAMILY_REPO[name] for name in names]
    stated, actual = int(cells[1]), sum(by_repo[repo] for repo in repos)
    assert stated == actual, (
        f"REPORT runnability row {cells[0]}: says {stated}, data has {actual}"
    )
    covered.extend(repos)
    claimed += stated

  # Exact-once coverage. Row counts summing to the total is not enough: naming
  # one family twice while omitting another of the same size keeps every per-row
  # count right and the total right, and still loses a family from the table.
  seen = Counter(covered)
  duplicated = {repo: n for repo, n in seen.items() if n > 1}
  missing = sorted(set(by_repo) - set(seen))
  assert not duplicated, (
      f"REPORT runnability table names these repos more than once: {duplicated}"
  )
  assert not missing, f"REPORT runnability table never names these repos: {missing}"
  assert claimed == total, (
      f"REPORT runnability table accounts for {claimed} of {total} instances - "
      "a family is missing from it"
  )
  return (
      f"REPORT runnability table checks out: {claimed} of {total} instances,"
      f" {len(seen)} families each named once"
  )
