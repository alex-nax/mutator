"""C and C++: sites, the CMake step, and how a mutant that does not build is counted.

The CMake tests here put a fake `cmake` and `ctest` first on PATH, so they
need no compiler. `test_c_cmake.py` runs the real toolchain.
"""

import json
import math
import os
import sys
import time
from pathlib import Path

import pytest

from mutator.cli import run
from mutator.edn import loads
from mutator.metrics import snapshot_path
from mutator.model import Site
from mutator.runner import CMAKE_STEP, CommandResult, is_cmake_step
from mutator.runner import test_plan as plan_for
from mutator.sites import discover_raw
from mutator.workers import classify, run_mutants


def _sites(language: str, source: str, path: str = "x.c") -> list[tuple[str, str, str]]:
    return [(site.original, site.mutant, site.category) for site in discover_raw(source, language, path)]


def test_c_mutates_comparisons_arithmetic_logic_bits_updates_and_returns():
    source = (
        "int f(int a, int b) {\n"
        "  a++; --b;\n"
        "  if (!a && b < 1 || a != 0) return a % 2 + (a & b) | (a ^ b) << 1 >> -a;\n"
        "  return ~a;\n"
        "}\n"
    )
    found = _sites("c", source)
    for expected in [
        ("++", "--", "update"),
        ("--", "++", "update"),
        ("!", "", "unary"),
        ("&&", "||", "logical"),
        ("||", "&&", "logical"),
        ("<", "<=", "comparison"),
        ("!=", "==", "equality"),
        ("%", "*", "arithmetic"),
        ("+", "-", "arithmetic"),
        ("&", "|", "bitwise"),
        ("|", "&", "bitwise"),
        ("^", "&", "bitwise"),
        ("<<", ">>", "bitwise"),
        (">>", "<<", "bitwise"),
        ("-", "", "unary"),
        ("~", "", "unary"),
        ("1", "0", "constant"),
        ("0", "1", "constant"),
        ("a % 2 + (a & b) | (a ^ b) << 1 >> -a", "0", "return"),
        ("~a", "0", "return"),
    ]:
        assert expected in found, expected


def test_cpp_mutates_booleans_alternative_tokens_and_lambdas():
    source = (
        "bool f(int a) {\n"
        "  auto l = [&](int q) { return q < a and not (q == 0); };\n"
        "  return a > 0 ? true : l(a);\n"
        "}\n"
    )
    found = _sites("cpp", source, "x.cpp")
    assert ("and", "or", "logical") in found
    assert ("not", "", "unary") in found
    assert ("true", "false", "boolean") in found
    assert ("q < a and not (q == 0)", "0", "return") in found


def test_preprocessor_conditions_and_macro_bodies_are_not_mutated():
    source = (
        "#define BIG(a) ((a) > 1)\n"
        "#if LIMIT > 1\n"
        "int f(int a) { return a + 1; }\n"
        "#elif LIMIT == 0\n"
        "int f(int a) { return a - 1; }\n"
        "#endif\n"
    )
    found = discover_raw(source, "c", "x.c")
    lines = {site.line for site in found}
    # Code inside an #if branch is code as written; the directives are not.
    assert lines == {3, 5}
    assert ("+", "-") in {(site.original, site.mutant) for site in found}


def test_a_return_is_not_mutated_into_itself():
    source = "void *f(void) { return NULL; }\nint g(void) { return 0x0; }\nint h(void) { return 1; }\n"
    returns = [site for site in discover_raw(source, "c", "x.c") if site.category == "return"]
    assert returns == []
    nonzero = "int f(void) { return 0x0f; }\nfloat g(void) { return 0.5f; }\nint h(void) { return 0x0UL; }\n"
    returns = [site.original for site in discover_raw(nonzero, "c", "x.c") if site.category == "return"]
    assert returns == ["0x0f", "0.5f"]


def test_c_only_rules_leave_the_other_languages_alone():
    python = _sites("python", "def f(a):\n    return a % 2 & 1\n", "x.py")
    assert all(original not in {"%", "&"} for original, _mutant, _category in python)
    java = _sites("java", "class A { int f(int a) { a++; return ~a; } }", "A.java")
    assert all(category not in {"update", "return"} for _o, _m, category in java)


def test_a_c_local_anchors_on_its_declaration_not_its_block():
    source = "int f(int a, int b) {\n  int z =\n     a > b;\n  return z;\n}\n"
    site = next(site for site in discover_raw(source, "c", "x.c") if site.original == ">")
    assert (site.line, site.anchor_line) == (3, 2)


def test_the_plan_runs_the_cmake_step_in_the_nearest_project(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(top C)\n", encoding="utf-8")
    module = tmp_path / "lib"
    (module / "src").mkdir(parents=True)
    (module / "CMakeLists.txt").write_text("add_library(x src/a.c)\n", encoding="utf-8")
    source = module / "src" / "a.c"
    source.write_text("int a(void) { return 2; }\n", encoding="utf-8")
    command, cwd = plan_for(tmp_path, source, "c", None)
    # lib/CMakeLists.txt has no project(), so the top project builds it.
    assert cwd == tmp_path.resolve()
    assert command == [sys.executable, CMAKE_STEP]
    assert is_cmake_step(command)
    assert not is_cmake_step(f"{sys.executable} {CMAKE_STEP}")
    (module / "CMakeLists.txt").write_text("project(lib C)\n", encoding="utf-8")
    assert plan_for(tmp_path, source, "cpp", None)[1] == module.resolve()


def test_only_the_generated_step_reads_125_as_a_build_failure():
    failed = CommandResult(code=125, timed_out=False, seconds=1, output="")
    assert classify([sys.executable, CMAKE_STEP], failed) == "discarded"
    assert classify("make test", failed) == "killed"
    tests_failed = CommandResult(code=8, timed_out=False, seconds=1, output="")
    assert classify([sys.executable, CMAKE_STEP], tests_failed) == "killed"
    passed = CommandResult(code=0, timed_out=False, seconds=1, output="")
    assert classify([sys.executable, CMAKE_STEP], passed) == "survived"


# A fake toolchain. configure writes a cache naming the source directory;
# the build fails on `return 0;`; ctest fails on `a - 1`. Every call is logged.
FAKE = """#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
with open(os.environ["FAKE_CMAKE_LOG"], "a") as log:
    log.write(json.dumps({{"tool": name, "cwd": os.getcwd(), "args": args}}) + "\\n")
def source_of(build):
    for line in (Path(build) / "CMakeCache.txt").read_text().splitlines():
        if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL="):
            return Path(line.split("=", 1)[1]) / "src" / "calc.c"
if name == "cmake" and "-S" in args:
    build = Path(args[args.index("-B") + 1])
    build.mkdir(parents=True, exist_ok=True)
    home = args[args.index("-S") + 1]
    (build / "CMakeCache.txt").write_text("CMAKE_HOME_DIRECTORY:INTERNAL=" + home + "\\n")
    sys.exit(0)
if name == "cmake" and "--build" in args:
    calc = source_of(args[args.index("--build") + 1])
    text = calc.read_text()
    if "NEEDS_GENERATED" in text and not (calc.parent.parent / "build" / "generated.h").exists():
        sys.exit(1)
    sys.exit(1 if "return 0;" in text or "BROKEN" in text else 0)
if name == "ctest":
    text = source_of(os.getcwd()).read_text()
    sys.exit(8 if "a - 1" in text or "FAILING" in text else 0)
"""

CALC = "int calc(int a) {\n  return a + 1;\n}\n"


@pytest.fixture
def fake_cmake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    for name in ("cmake", "ctest"):
        tool = bin_dir / name
        tool.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
        tool.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CMAKE_LOG", str(log))
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "CMakeLists.txt").write_text("project(fake C)\n", encoding="utf-8")
    (project / "src" / "calc.c").write_text(CALC, encoding="utf-8")

    def calls(tool: str, flag: str) -> list[dict]:
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row["tool"] == tool and flag in row["args"]]

    return project, calls


def _mutate(project: Path, *extra: str) -> int:
    return run(["--no-coverage", "--mutate-all", "--root", str(project), *extra, "src/calc.c"])


def test_a_mutant_that_does_not_build_is_discarded_not_killed(fake_cmake, capsys):
    project, _calls = fake_cmake
    code = _mutate(project, "--max-workers", "3")
    out = capsys.readouterr().out
    assert "KILLED    src/calc.c:2 + -> -" in out
    assert "SURVIVED  src/calc.c:2 1 -> 0" in out
    assert "DISCARDED src/calc.c:2 a + 1 -> 0" in out
    assert code == 3
    snapshot = loads(snapshot_path(project, "src/calc").read_text(encoding="utf-8"))
    form = snapshot["forms"][0]
    assert (form["killed"], form["survived"], form["discarded"], form["sites"]) == (1, 1, 1, 3)
    assert sorted(snapshot["outcomes"].values()) == ["discarded", "killed", "survived"]


def test_a_discarded_mutant_is_not_rebuilt_while_its_function_is_unchanged(fake_cmake, capsys):
    project, calls = fake_cmake
    _mutate(project)
    capsys.readouterr()
    before = len(calls("cmake", "--build"))
    run(["--no-coverage", "--root", str(project), "src/calc.c"])
    out = capsys.readouterr().out
    assert "DISCARDED src/calc.c:2 a + 1 -> 0" in out
    # The baseline, the warm-up, and the one survivor rerun. Not the discarded one.
    assert len(calls("cmake", "--build")) - before == 3


def test_each_worker_builds_in_its_own_directory(fake_cmake, capsys):
    project, calls = fake_cmake
    _mutate(project, "--max-workers", "3")
    capsys.readouterr()
    configured = calls("cmake", "-S")
    assert len(configured) == 4, "the baseline plus three workers"
    baseline = configured[0]["args"]
    assert Path(baseline[baseline.index("-B") + 1]) == project.resolve() / "target" / "mutator-build"
    workers = {}
    for row in configured[1:]:
        args = row["args"]
        source = Path(args[args.index("-S") + 1])
        build = Path(args[args.index("-B") + 1])
        assert "mutation-workers" in source.parts
        assert build.is_relative_to(source), "a worker builds inside its own overlay"
        workers[source] = build
    assert len(set(workers)) == 3
    assert len(set(workers.values())) == 3


def test_a_baseline_that_fails_to_build_or_test_stops_with_exit_2(fake_cmake, capsys):
    project, _calls = fake_cmake
    source = project / "src" / "calc.c"
    for marker in ("BROKEN", "FAILING"):
        source.write_text(f"/* {marker} */\n" + CALC, encoding="utf-8")
        code = _mutate(project)
        captured = capsys.readouterr()
        assert code == 2
        assert "Baseline failed for src/calc.c" in captured.err
        assert "KILLED" not in captured.out
        assert not snapshot_path(project, "src/calc").exists()


def test_a_worker_that_cannot_build_unmutated_stops_the_file_instead_of_discarding(fake_cmake, capsys):
    project, _calls = fake_cmake
    # A worker never links `build/`, so a project that compiles from it cannot build there.
    (project / "build").mkdir()
    (project / "build" / "generated.h").write_text("", encoding="utf-8")
    (project / "src" / "calc.c").write_text("/* NEEDS_GENERATED */\n" + CALC, encoding="utf-8")
    code = _mutate(project, "--max-workers", "2")
    captured = capsys.readouterr()
    assert code == 2
    assert "The unmutated build failed in worker" in captured.err
    assert "DISCARDED" not in captured.out
    assert not snapshot_path(project, "src/calc").exists()


def _site(source: str, original: str, mutant: str) -> Site:
    start = source.index(original)
    return Site(
        file="src/calc.c",
        namespace="src/calc",
        form_id="defn/calc",
        name="calc",
        line=2,
        start=start,
        end=start + len(original),
        original=original,
        mutant=mutant,
        category="arithmetic",
        mutation_id=f"{original}->{mutant}",
    )


class _Recorder:
    """Records the worker copy each mutant saw, and when each run ended."""

    verbose = False

    def __init__(self, fail_on: str | None = None):
        self.seen: list[tuple[str, float, float]] = []
        self.fail_on = fail_on

    def run(self, command, cwd, timeout):
        copy = cwd / "src" / "calc.c"
        text = copy.read_text(encoding="utf-8")
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("the runner broke")
        self.seen.append((text, copy.stat().st_mtime, time.time()))
        return CommandResult(code=0, timed_out=False, seconds=0.01, output="")


def _run_mutants(project: Path, sites: list[Site], runner) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    source = project / "src" / "calc.c"
    failure = run_mutants(
        project,
        source,
        source.read_bytes(),
        sites,
        1,
        runner,
        [sys.executable, CMAKE_STEP],
        project,
        5.0,
        "src/calc.c",
        outcomes,
    )
    assert failure is None
    return outcomes


def test_the_worker_copy_is_restored_after_each_mutant_even_when_the_run_breaks(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "calc.c").write_text(CALC, encoding="utf-8")
    copies: list[Path] = []
    import mutator.workers as workers

    real_create = workers.create_workers

    def keep(*args, **kwargs):
        created = real_create(*args, **kwargs)
        copies.extend(directory / "src" / "calc.c" for directory in created)
        return created

    monkeypatch.setattr(workers, "create_workers", keep)
    monkeypatch.setattr(workers, "delete_tree", lambda path: None)
    _run_mutants(project, [_site(CALC, "+", "-")], _Recorder())
    assert copies[0].read_text(encoding="utf-8") == CALC
    with pytest.raises(RuntimeError):
        _run_mutants(project, [_site(CALC, "1", "0")], _Recorder(fail_on="a + 0"))
    assert copies[1].read_text(encoding="utf-8") == CALC
    assert (project / "src" / "calc.c").read_text(encoding="utf-8") == CALC


def test_a_mutant_is_written_a_whole_second_after_the_previous_build(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "calc.c").write_text(CALC, encoding="utf-8")
    recorder = _Recorder()
    sites = [_site(CALC, "+", "-"), _site(CALC, "1", "0"), _site(CALC, "a + 1", "0")]
    _run_mutants(project, sites, recorder)
    # The first call is the unmutated warm-up build.
    assert recorder.seen[0][0] == CALC
    assert len(recorder.seen) == 4
    for (_text, _mtime, ended), (mutated, mtime, _end) in zip(recorder.seen, recorder.seen[1:]):
        assert mutated != CALC
        # Make 3.81 compares whole seconds: the mutant must be newer than any object.
        assert math.floor(mtime) > math.floor(ended)


def test_a_static_c_function_is_private_and_a_c_namespace_is_its_relative_path(tmp_path):
    from mutator.functions import is_private, sites_in_file

    assert is_private("c", "f", "static int f(void) { return 2; }\n", 1, 1)
    assert is_private("c", "f", "inline static int f(void) { return 2; }\n", 1, 1)
    assert not is_private("c", "f", "int f(void) { return 2; }\n", 1, 1)
    assert not is_private("cpp", "f", "static int f() { return 2; }\n", 1, 1)
    path = tmp_path / "src" / "calc.c"
    path.parent.mkdir()
    source = "static int hide(int a) { return a + 2; }\n"
    path.write_text(source, encoding="utf-8")
    site = sites_in_file(source, path, tmp_path, "src/calc.c")[0]
    assert (site.namespace, site.form_id) == ("src/calc", "defn-/hide")
