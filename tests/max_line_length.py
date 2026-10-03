"""Fail on any line longer than 80 characters, without exemptions.

Ruff's E501 skips a line that is one unbreakable token, ends in a URL, or ends
in a pragma comment, so a clean E501 does not mean every line fits. This is the
pre-commit check that does: it counts characters, not bytes, and exempts
nothing. A line that genuinely cannot be wrapped is rewritten (split string,
comment moved above) rather than excused here.
"""

import sys

MAX_LENGTH = 80


def main(paths: list[str]) -> int:
  """Print every over-long line in ``paths``; return 1 if there was one."""
  found = False
  for path in paths:
    with open(path, encoding="utf-8") as file:
      for number, line in enumerate(file, start=1):
        line = line.rstrip("\r\n")
        if len(line) > MAX_LENGTH:
          print(f"{path}:{number}: {len(line)} > {MAX_LENGTH} characters")
          found = True
  return int(found)


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
