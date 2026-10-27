"""The real toolchain: CMake, a C compiler, ctest, and crapper's C coverage.

The fixture is copied out of `tests/`, because a path with a `tests` part is
a test file to crapper, and so the build stays out of this repository.
"""

import shutil
from pathlib import Path

import pytest

from mutator.cli import run
from mutator.crapper_link import ensure_crapper
from mutator.edn import loads
from mutator.metrics import snapshot_path

FIXTURE = Path(__file__).parent / "fixtures" / "cmake_c"


def _missing_tools() -> str | None:
    for tool in ("cmake", "ctest", "cc"):
        if shutil.which(tool) is None:
            return f"{tool} is not installed"
    coverage = getattr(ensure_crapper(), "cmake_coverage", None)
    if coverage is None:
        return "the crapper next to mutator has no C/C++ support (its c-cpp branch)"
    find_tool = coverage.find_tool
    if not (find_tool("llvm-cov", None) or find_tool("gcov", None)):
        return "neither llvm-cov nor gcov is installed, so there is no C coverage"
    return None


MISSING = _missing_tools()

EXPECTED = {
    "src/calc.c:4 > -> >=": "KILLED",
    "src/calc.c:5 a * 2 - b -> 0": "KILLED",
    "src/calc.c:5 * -> /": "KILLED",
    "src/calc.c:5 - -> +": "KILLED",
    "src/calc.c:7 b - a -> 0": "KILLED",
    "src/calc.c:7 - -> +": "KILLED",
    # clamp_low is called, but never with value == floor.
    "src/calc.c:11 < -> <=": "SURVIVED",
    "src/calc.c:12 floor -> 0": "KILLED",
    "src/calc.c:14 value -> 0": "KILLED",
    # A function returning a struct cannot return 0: the compiler refuses it.
    "src/calc.c:21 p -> 0": "DISCARDED",
}


def _statuses(out: str) -> dict[str, str]:
    found = {}
    for line in out.splitlines():
        label, _space, rest = line.partition(" ")
        if label in {"KILLED", "SURVIVED", "DISCARDED", "UNCOVERED"}:
            found[rest.strip()] = label
    return found


@pytest.mark.skipif(MISSING is not None, reason=MISSING or "")
def test_a_real_cmake_project_kills_keeps_and_discards_mutants(tmp_path, capfd):
    project = tmp_path / "calc"
    shutil.copytree(FIXTURE, project)
    source = project / "src" / "calc.c"
    original = source.read_bytes()

    code = run(["--root", str(project), "--max-workers", "2"])
    out = capfd.readouterr().out
    assert _statuses(out) == EXPECTED
    assert code == 3
    assert source.read_bytes() == original
    assert not any((project / "target" / "mutation-workers").iterdir())
    snapshot = loads(snapshot_path(project, "src/calc").read_text(encoding="utf-8"))
    forms = {form["id"]: form for form in snapshot["forms"]}
    assert (forms["defn/weighted"]["killed"], forms["defn/weighted"]["survived"]) == (6, 0)
    assert (forms["defn/clamp_low"]["killed"], forms["defn/clamp_low"]["survived"]) == (2, 1)
    assert forms["defn/make_pair"]["discarded"] == 1
    assert forms["defn/make_pair"]["survived"] == 0

    # The second run builds the real tree warm, so its baseline is fast. The
    # survivor is rerun in a cold worker and must still be judged by its tests.
    code = run(["--root", str(project), "--max-workers", "2", "--use-existing-coverage"])
    out = capfd.readouterr().out
    assert _statuses(out)["src/calc.c:11 < -> <="] == "SURVIVED"
    assert code == 3
    assert source.read_bytes() == original
