"""Every response that reports a count reports it the same way.

`shared/counts.py` landed here wired into one call site -- `fs_index list`,
which had a `truncated` flag naming no total. The contract says the shared
helper is the only emitter, and one of seven is not that. Wiring the rest found
three more:

* **`fs_read` mode=tree** had the identical defect to `fs_index list`:
  `truncated: true` and no denominator anywhere. The walk deliberately stops one
  entry past the cap, so the true total is genuinely unknown and counting it
  costs the same walk again -- `counted(..., exact=False)` marks `total` a floor
  rather than inventing a number or omitting one.
* **`fs_index query`** reported `indexed_under_root` beside `truncated`, which
  reads like the denominator and is not: it counts what the index holds under
  that root, not what the pattern matched.
* **`fs_read` mode=content** measured truncation against the end of the file, so
  reading lines 50-100 of a 100-line file came back `truncated: true` having
  returned every line asked for. Nothing was withheld. The window is now the
  denominator, and "the file continues past here" -- a real and useful signal --
  keeps its own name, `more_after`, instead of borrowing this one.

## The composite exemption

`fs_query` in grep mode carries two payloads cut by two different budgets: the
file list by max_results, the lines inside each file by the hit budget. One
derived flag cannot describe both, and a test in
`test_a_grep_bounds_its_lines.py` pins the behaviour that matters -- 8,000
clipped lines must not come back as a complete result. So that response keeps a
composite `truncated` written by hand, marked `# counts-contract: composite`,
and the static rule below permits exactly that marker and nothing else. The
marker is greppable, so the exemptions can be counted; a silent exception could
not be.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ROOT / "servers"
SHARED = ROOT / "shared"

_HAND_WRITTEN = re.compile(r'"truncated"\s*:')
_EXEMPT = "counts-contract: composite"
# How far back to look for the marker, so it can sit above a real explanation.
_LOOKBACK = 15


def _py_files() -> list[Path]:
    files = [p for p in SERVERS.rglob("*.py") if "__pycache__" not in p.parts]
    files += [p for p in SHARED.rglob("*.py") if "__pycache__" not in p.parts]
    return [p for p in files if p.name != "counts.py"]


def _offenders() -> list[str]:
    out: list[str] = []
    for path in _py_files():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue  # modules quote the banned string while explaining it
            if not _HAND_WRITTEN.search(line):
                continue
            window = "\n".join(lines[max(0, i - _LOOKBACK) : i])
            if _EXEMPT in window:
                continue
            out.append(f"{path.relative_to(ROOT)}:{i + 1}: {line.strip()}")
    return out


def test_no_module_writes_the_truncated_key_by_hand():
    offenders = _offenders()
    assert not offenders, (
        "these write `truncated` by hand instead of calling counted():\n  "
        + "\n  ".join(offenders)
        + "\n\ncounted(returned, total) derives it, so the flag cannot disagree "
        "with the numbers printed beside it. If a response genuinely has two "
        f"payloads, mark the line `# {_EXEMPT}` and say why."
    )


def test_the_exemption_stays_rare_and_explains_itself():
    """One marker today. A second needs a reason someone chose to write down."""
    marked = []
    for path in _py_files():
        text = path.read_text()
        if _EXEMPT in text:
            marked.append(path.relative_to(ROOT))
            assert "counted(" in text, (
                f"{path.name} claims a composite flag but never calls counted()"
            )
    assert len(marked) <= 1, f"composite flags are meant to be exceptional, found: {marked}"


def test_a_composite_flag_still_ships_the_per_payload_numbers():
    """The override is allowed because nothing is hidden by it."""
    src = (SERVERS / "fs_basic" / "_basic_query.py").read_text()
    for field in (
        "files_matched",
        "files_truncated",
        "hits_returned",
        "hits_found",
        "hits_truncated",
    ):
        assert f'"{field}"' in src, f"a composite truncated must still report {field}"
