"""How a ``claude_code`` run is configured: its capture mode and its effort.

Both are closed sets of strings the agent's own CLI defines, so they are
``Literal`` aliases rather than enums: the value *is* the flag text, it carries
no behavior of its own, and a caller writes ``capture="proxy"`` without
importing anything. Validation still happens where it matters — the CLI
override engine checks a value against the alias's members before a container
is paid for, so a typo in a sweep config is refused rather than silently
accepted.

(The engine's domain states — ``RunStatus``, ``TaskOutcome``, ``EntryStatus``
— stay enums. Those are branched on with ``is`` and mean something to the code;
these two only ever travel to a command line.)
"""

from __future__ import annotations

from typing import Literal

type Capture = Literal["stream", "proxy"]
"""How a run captures its agent trace.

- ``"stream"`` — the agent's stdout becomes ``claude.event_stream.jsonl``
  (the default). The run passes ``--replay-user-messages``, so the stream
  carries every user message the agent received — the run's own opening prompt
  and anything injected mid-run — rather than only what the agent said back.
- ``"proxy"`` — a host-side ``cc-reverse-proxy`` records request/response
  instead, and the agent's own stdout is discarded.
"""

type Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""How much reasoning effort a run is told to spend (``--effort``).

Exactly the set the pinned agent accepts, read off the binary rather than a
doc — 2.1.220 answers an unknown value with::

    Warning: Unknown --effort value 'bogus' — ignoring it and using the
    default effort. Valid values: low, medium, high, xhigh, max.

That warning is why the value is checked on the way in. An unrecognized effort
is **not** an error to the agent: it shrugs and runs at its default, so a typo
in a sweep config would produce a whole batch at the wrong effort with nothing
in the logs to say so.
"""
