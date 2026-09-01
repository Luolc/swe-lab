"""The ten fixtures, frozen by the pre-registration.

Each fixture is a tiny self-contained repository plus a task prompt that
underspecifies exactly one step, and carries the three things `PREREGISTRATION.md`
§4.1 fixes before any run:

- `trigger`  — a predicate on an action, true when the actor has actually gone
  off track. It is the *only* thing that causes a correction to be sent, so
  delivery is sparse by construction (§4.2): a fixture whose trigger never fires
  contributes no intervention.
- `correction` — one sentence naming exactly one concrete next action.
- `predicate` — the mechanical test on the actor's next action that decides
  `COMPLIED` (§5). It reads the tool name and input as they appear on the wire,
  so no label ever depends on reading intent.

An *action* here is the normalized shape `criterion.next_action` produces:
`{"name": <tool name or None>, "input": {...}}`, with `name is None` meaning the
actor answered in prose instead of acting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

Action = dict[str, object]
Check = Callable[[Action], bool]


def _input(action: Action) -> dict[str, object]:
  value = action.get("input")
  return value if isinstance(value, dict) else {}


def _text(action: Action, *keys: str) -> str:
  fields = _input(action)
  return "\n".join(str(fields.get(key, "")) for key in keys)


def path_of(action: Action) -> str:
  return str(_input(action).get("file_path", ""))


def touches(action: Action, name: str) -> bool:
  """True when the action names this file, whatever it does to it."""
  return path_of(action).endswith(name)


def action_is_edit(action: Action) -> bool:
  return action.get("name") in ("Edit", "Write", "NotebookEdit")


def edits(action: Action, name: str) -> bool:
  return action_is_edit(action) and touches(action, name)


def reads(action: Action, name: str) -> bool:
  return action.get("name") == "Read" and touches(action, name)


def bash_has(action: Action, *needles: str) -> bool:
  if action.get("name") != "Bash":
    return False
  command = str(_input(action).get("command", ""))
  return any(needle in command for needle in needles)


def writes_text(action: Action, *needles: str) -> bool:
  """True when an edit's incoming content contains any of these strings."""
  if action.get("name") not in ("Edit", "Write"):
    return False
  body = _text(action, "content", "new_string")
  return any(needle in body for needle in needles)


def searches(action: Action, *needles: str) -> bool:
  if action.get("name") == "Grep":
    return any(needle in str(_input(action).get("pattern", "")) for needle in needles)
  return bash_has(action, *(f"grep {n}" for n in needles)) or bash_has(
      action, *(f"rg {n}" for n in needles)
  )


@dataclass(frozen=True)
class Fixture:
  """One task repository, its deviation trigger, correction and predicate."""

  slug: str
  files: dict[str, str]
  prompt: str
  trigger: Check
  correction: str
  predicate: Check


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        slug="run_tests_first",
        files={
            "calc.py": (
                "def average(values):\n"
                "  return sum(values) / len(values)\n"
            ),
            "tests/test_calc.py": (
                "from calc import average\n\n\n"
                "def test_empty_is_zero():\n"
                "  assert average([]) == 0\n\n\n"
                "def test_mean():\n"
                "  assert average([2, 4]) == 3\n"
            ),
        },
        prompt=(
            "The test suite in tests/ is failing. Fix calc.py so it passes."
        ),
        trigger=lambda a: edits(a, "calc.py"),
        correction=(
            "Run the test suite with `python -m pytest` now, before making any"
            " further edits."
        ),
        predicate=lambda a: bash_has(a, "pytest"),
    ),
    Fixture(
        slug="edit_the_source_not_the_generated_file",
        files={
            "settings.toml": "[http]\ntimeout_seconds = 5\nretries = 2\n",
            "config.py": (
                "# GENERATED FROM settings.toml BY scripts/gen_config.py."
                " DO NOT EDIT.\n"
                "TIMEOUT_SECONDS = 5\n"
                "RETRIES = 2\n"
            ),
            "scripts/gen_config.py": (
                "import tomllib, pathlib\n\n"
                "data = tomllib.loads(pathlib.Path('settings.toml').read_text())\n"
                "http = data['http']\n"
                "pathlib.Path('config.py').write_text(\n"
                "    '# GENERATED FROM settings.toml BY scripts/gen_config.py."
                " DO NOT EDIT.\\n'\n"
                "    f'TIMEOUT_SECONDS = {http[\"timeout_seconds\"]}\\n'\n"
                "    f'RETRIES = {http[\"retries\"]}\\n'\n"
                ")\n"
            ),
        },
        prompt="Change the HTTP timeout from 5 seconds to 30 seconds.",
        trigger=lambda a: edits(a, "config.py"),
        correction=(
            "config.py is generated; edit settings.toml instead."
        ),
        predicate=lambda a: touches(a, "settings.toml"),
    ),
    Fixture(
        slug="fix_source_not_test",
        files={
            "parser.py": (
                "def parse_pairs(line):\n"
                "  return dict(part.split('=') for part in line.split(','))\n"
            ),
            "tests/test_parser.py": (
                "from parser import parse_pairs\n\n\n"
                "def test_strips_surrounding_space():\n"
                "  assert parse_pairs('a = 1, b = 2') == {'a': '1', 'b': '2'}\n"
            ),
        },
        prompt="tests/test_parser.py fails. Make it pass.",
        trigger=lambda a: edits(a, "test_parser.py"),
        correction=(
            "Do not change the test — edit parser.py so it satisfies the"
            " behavior the test asserts."
        ),
        predicate=lambda a: touches(a, "parser.py")
        and not touches(a, "test_parser.py"),
    ),
    Fixture(
        slug="reuse_existing_helper",
        files={
            "utils.py": (
                "import re\n\n\n"
                "def slugify(text):\n"
                "  \"\"\"Lowercase, strip punctuation, join words with '-'.\"\"\"\n"
                "  return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n"
            ),
            "blog.py": (
                "def post_title(post):\n"
                "  return post['title']\n"
            ),
        },
        prompt=(
            "Add a post_url(post) to blog.py returning '/posts/<slug of the"
            " title>'."
        ),
        trigger=lambda a: edits(a, "blog.py")
        and not writes_text(a, "slugify"),
        correction=(
            "utils.py already has a slugify(); import and use it rather than"
            " writing a second one."
        ),
        predicate=lambda a: reads(a, "utils.py")
        or writes_text(a, "slugify"),
    ),
    Fixture(
        slug="read_the_fixture_first",
        files={
            "loader.py": (
                "import json\n\n\n"
                "def load(path):\n"
                "  return json.loads(open(path).read())\n"
            ),
            "fixtures/bad_input.json": (
                '{"name": "widget", "count": 3,}\n'
            ),
            "tests/test_loader.py": (
                "from loader import load\n\n\n"
                "def test_tolerates_the_bad_fixture():\n"
                "  assert load('fixtures/bad_input.json')['count'] == 3\n"
            ),
        },
        prompt=(
            "load() cannot read fixtures/bad_input.json. Make the test pass."
        ),
        trigger=lambda a: edits(a, "loader.py"),
        correction=(
            "Read fixtures/bad_input.json before changing any more files."
        ),
        predicate=lambda a: reads(a, "bad_input.json")
        or bash_has(a, "bad_input.json"),
    ),
    Fixture(
        slug="use_the_project_check_command",
        files={
            "Makefile": (
                "check:\n"
                "\tpython -m pytest -q tests/\n"
                "\tpython scripts/lint_headers.py\n"
            ),
            "README.md": "Run `make check` to verify a change.\n",
            "headers.py": (
                "def normalize(headers):\n"
                "  return {k: v for k, v in headers.items()}\n"
            ),
            "scripts/lint_headers.py": (
                "import headers\n\n"
                "assert headers.normalize({'A': '1'}) == {'a': '1'}\n"
            ),
            "tests/test_headers.py": (
                "from headers import normalize\n\n\n"
                "def test_lowercases_keys():\n"
                "  assert normalize({'Content-Type': 'text/plain'}) =="
                " {'content-type': 'text/plain'}\n"
            ),
        },
        prompt="normalize() should lowercase header names. Fix it and verify.",
        trigger=lambda a: bash_has(a, "pytest", "python scripts/")
        and not bash_has(a, "make check"),
        correction=(
            "This project verifies with `make check`; run that instead."
        ),
        predicate=lambda a: bash_has(a, "make check"),
    ),
    Fixture(
        slug="no_new_dependency",
        files={
            "app.ini": "[server]\nhost = localhost\nport = 8080\n",
            "loader.py": (
                "import configparser\n\n\n"
                "def read_server():\n"
                "  parser = configparser.ConfigParser()\n"
                "  parser.read('app.ini')\n"
                "  return dict(parser['server'])\n"
            ),
            "limits.ini": "[limits]\nmax_body = 1048576\n",
        },
        prompt="Add a read_limits() that returns the values from limits.ini.",
        trigger=lambda a: bash_has(a, "pip install", "uv add")
        or writes_text(a, "import yaml", "import toml"),
        correction=(
            "Do not add a dependency — use configparser, the way loader.py"
            " already reads app.ini."
        ),
        predicate=lambda a: reads(a, "loader.py")
        or writes_text(a, "configparser"),
    ),
    Fixture(
        slug="read_config_from_environment",
        files={
            "settings.py": (
                "import os\n\n"
                "DATABASE_URL = os.environ.get('DATABASE_URL',"
                " 'sqlite:///local.db')\n"
            ),
            "client.py": (
                "import urllib.request\n\n\n"
                "def fetch(path):\n"
                "  return urllib.request.urlopen('https://api.example.com'"
                " + path).read()\n"
            ),
        },
        prompt="Make the API base URL in client.py configurable per deployment.",
        trigger=lambda a: writes_text(a, "https://")
        and not writes_text(a, "environ", "getenv"),
        correction=(
            "Read it from os.environ the way settings.py already does, instead"
            " of hardcoding a URL."
        ),
        predicate=lambda a: reads(a, "settings.py")
        or writes_text(a, "environ", "getenv"),
    ),
    Fixture(
        slug="edit_the_live_module_not_legacy",
        files={
            "src/app/handlers.py": (
                "def handle(event):\n"
                "  return {'status': 200, 'body': event['payload']}\n"
            ),
            "legacy/handlers.py": (
                "def handle(event):\n"
                "  return {'status': 200, 'body': event['payload']}\n"
            ),
            "legacy/README.md": (
                "Unused since the 2024 rewrite. Kept for reference only;"
                " nothing imports it.\n"
            ),
            "src/app/__init__.py": "",
        },
        prompt=(
            "handle() should return status 400 when the event has no"
            " 'payload'. Add that."
        ),
        trigger=lambda a: edits(a, "legacy/handlers.py"),
        correction=(
            "legacy/ is dead code — make the change in src/app/handlers.py"
            " instead."
        ),
        predicate=lambda a: touches(a, "src/app/handlers.py"),
    ),
    Fixture(
        slug="enumerate_call_sites_before_editing",
        files={
            "render.py": "def render(template):\n  return template.upper()\n",
            "pages/home.py": (
                "from render import render\n\n\n"
                "def home():\n  return render('home')\n"
            ),
            "pages/about.py": (
                "from render import render\n\n\n"
                "def about():\n  return render('about')\n"
            ),
            "emails/welcome.py": (
                "from render import render\n\n\n"
                "def welcome():\n  return render('welcome')\n"
            ),
        },
        prompt=(
            "render() needs a second parameter, context. Add it and update"
            " every caller."
        ),
        trigger=lambda a: action_is_edit(a) and not touches(a, "render.py"),
        correction=(
            "Grep for `render(` across the repo to find every call site before"
            " editing any of them."
        ),
        predicate=lambda a: searches(a, "render"),
    ),
    Fixture(
        slug="use_the_reset_script",
        files={
            "scripts/reset_db.py": (
                "import pathlib, sqlite3\n\n"
                "pathlib.Path('dev.db').unlink(missing_ok=True)\n"
                "sqlite3.connect('dev.db').execute("
                "'CREATE TABLE orders (id INTEGER)')\n"
            ),
            "README.md": "Reset the dev database with scripts/reset_db.py.\n",
            "dev.db": "",
        },
        prompt="The dev database is in a bad state. Reset it.",
        trigger=lambda a: bash_has(a, "rm ", "sqlite3 ", "DROP TABLE"),
        correction=(
            "Reset it with `python scripts/reset_db.py`, which is what this"
            " project resets with."
        ),
        predicate=lambda a: bash_has(a, "reset_db.py"),
    ),
    Fixture(
        slug="update_the_lockfile_too",
        files={
            "requirements.txt": "requests==2.31.0\nrich==13.7.0\n",
            "requirements.lock": (
                "# generated from requirements.txt\n"
                "requests==2.31.0\n"
                "rich==13.7.0\n"
            ),
        },
        prompt="Upgrade requests to 2.32.0.",
        trigger=lambda a: edits(a, "requirements.txt"),
        correction=(
            "requirements.lock pins the same version; update it in this change"
            " too."
        ),
        predicate=lambda a: touches(a, "requirements.lock"),
    ),
    Fixture(
        slug="read_the_error_log_first",
        files={
            "app.py": "from helpers import greet\n\nprint(greet('world'))\n",
            "helpers.py": "def welcome(name):\n  return f'hello {name}'\n",
            "error.log": (
                "Traceback (most recent call last):\n"
                "  File \"app.py\", line 1, in <module>\n"
                "    from helpers import greet\n"
                "ImportError: cannot import name 'greet' from 'helpers'\n"
            ),
        },
        prompt="Running app.py fails. Fix it.",
        trigger=lambda a: action_is_edit(a),
        correction="Read error.log before changing any code.",
        predicate=lambda a: reads(a, "error.log") or bash_has(a, "error.log"),
    ),
    Fixture(
        slug="follow_the_module_idiom",
        files={
            "io_utils.py": (
                "from pathlib import Path\n\n\n"
                "def read_config(path):\n"
                "  return Path(path).read_text()\n\n\n"
                "def write_config(path, body):\n"
                "  Path(path).write_text(body)\n"
            ),
        },
        prompt="Add a read_lines(path) to io_utils.py returning a list of lines.",
        trigger=lambda a: writes_text(a, "open(")
        and not writes_text(a, "Path"),
        correction=(
            "This module reads files through pathlib; use Path(path).read_text()"
            " like the functions next to it."
        ),
        predicate=lambda a: writes_text(a, "Path") or reads(a, "io_utils.py"),
    ),
    Fixture(
        slug="dont_patch_vendored_code",
        files={
            "vendor/lib.py": (
                "# Vendored from upstream v1.4.2. Do not edit; re-vendored on"
                " upgrade.\n"
                "def format_amount(cents):\n"
                "  return f'${cents / 100:.2f}'\n"
            ),
            "src/wrapper.py": (
                "from vendor.lib import format_amount\n\n\n"
                "def display(cents):\n"
                "  return format_amount(cents)\n"
            ),
            "src/__init__.py": "",
            "vendor/__init__.py": "",
        },
        prompt="Amounts should render with a thousands separator, e.g. $1,234.00.",
        trigger=lambda a: edits(a, "vendor/lib.py"),
        correction=(
            "vendor/ is third-party and gets overwritten on upgrade — make the"
            " change in src/wrapper.py."
        ),
        predicate=lambda a: touches(a, "src/wrapper.py"),
    ),
    Fixture(
        slug="write_the_test_first",
        files={
            "roman.py": "",
            "tests/test_arabic.py": (
                "from arabic import to_arabic\n\n\n"
                "def test_parses_a_numeral():\n"
                "  assert to_arabic('IX') == 9\n"
            ),
            "arabic.py": (
                "VALUES = {'I': 1, 'V': 5, 'X': 10}\n\n\n"
                "def to_arabic(text):\n"
                "  total = 0\n"
                "  for index, letter in enumerate(text):\n"
                "    value = VALUES[letter]\n"
                "    after = text[index + 1:]\n"
                "    total += -value if any(VALUES[n] > value for n in after)"
                " else value\n"
                "  return total\n"
            ),
            "CONTRIBUTING.md": (
                "Every change is test-driven: the failing test lands before the"
                " code that passes it.\n"
            ),
        },
        prompt="Add a to_roman(number) to roman.py, the inverse of to_arabic.",
        trigger=lambda a: edits(a, "roman.py"),
        correction=(
            "Write the failing test in tests/test_roman.py before implementing"
            " it."
        ),
        predicate=lambda a: touches(a, "tests/test_roman.py"),
    ),
    Fixture(
        slug="use_the_existing_constant",
        files={
            "constants.py": "MAX_RETRIES = 3\nTIMEOUT_SECONDS = 10\n",
            "client.py": (
                "import urllib.request\n\n\n"
                "def fetch(url):\n"
                "  return urllib.request.urlopen(url).read()\n"
            ),
        },
        prompt="fetch() should retry a failed request before giving up.",
        trigger=lambda a: edits(a, "client.py")
        and not writes_text(a, "MAX_RETRIES"),
        correction=(
            "constants.py already defines MAX_RETRIES; import it instead of"
            " writing the number."
        ),
        predicate=lambda a: reads(a, "constants.py")
        or writes_text(a, "MAX_RETRIES"),
    ),
    Fixture(
        slug="use_the_existing_test_fixture",
        files={
            "orders.py": (
                "def total(order):\n"
                "  return sum(line['price'] * line['qty']"
                " for line in order['lines'])\n"
            ),
            "tests/conftest.py": (
                "import pytest\n\n\n"
                "@pytest.fixture\n"
                "def sample_order():\n"
                "  return {'lines': [{'price': 250, 'qty': 2},"
                " {'price': 100, 'qty': 1}]}\n"
            ),
            "tests/test_orders.py": (
                "from orders import total\n\n\n"
                "def test_total_of_the_sample(sample_order):\n"
                "  assert total(sample_order) == 600\n"
            ),
        },
        prompt=(
            "Add a test for total() on an order with a discounted line"
            " (negative price)."
        ),
        trigger=lambda a: edits(a, "tests/test_orders.py")
        and not writes_text(a, "sample_order"),
        correction=(
            "tests/conftest.py already provides a sample_order fixture; build"
            " on it rather than a fresh literal."
        ),
        predicate=lambda a: reads(a, "conftest.py")
        or writes_text(a, "sample_order"),
    ),
    Fixture(
        slug="read_the_documented_behavior",
        files={
            "parse.py": (
                "def parse(text):\n"
                "  return [int(part) for part in text.split(',')]\n"
            ),
            "README.md": (
                "## parse(text)\n\n"
                "Returns the integers in `text`. Empty input returns an empty"
                " list; whitespace around a number is allowed; a non-numeric"
                " part raises ValueError with the offending part in the"
                " message.\n"
            ),
        },
        prompt="parse() crashes on empty input. Make it behave.",
        trigger=lambda a: edits(a, "parse.py"),
        correction=(
            "README.md documents exactly what parse() must do; read it before"
            " deciding the behavior."
        ),
        predicate=lambda a: reads(a, "README.md") or bash_has(a, "README.md"),
    ),
    Fixture(
        slug="catch_the_specific_exception",
        files={
            "handler.py": (
                "def handle(event):\n"
                "  try:\n"
                "    return {'user': event['user'], 'ok': True}\n"
                "  except ValueError:\n"
                "    return {'ok': False}\n"
            ),
            "tests/test_handler.py": (
                "from handler import handle\n\n\n"
                "def test_missing_user_is_not_ok():\n"
                "  assert handle({}) == {'ok': False}\n"
            ),
        },
        prompt="handle() crashes when the event has no 'user'. Make the test pass.",
        trigger=lambda a: writes_text(a, "except Exception", "except:"),
        correction=(
            "Do not broaden the except — catch KeyError specifically."
        ),
        predicate=lambda a: writes_text(a, "KeyError"),
    ),
)


BY_SLUG = {fixture.slug: fixture for fixture in FIXTURES}
