"""Build and test the CMake project in the current directory, for one mutant.

Run as ``python cmake_step.py [--test-timeout SECONDS]`` with the CMake
project (the directory whose ``CMakeLists.txt`` calls ``project(``) as the
working directory. The build directory is ``target/mutator-build`` under that
working directory. A worker runs this inside its own overlay, so each worker
builds into its own directory and compiles its own copy of the mutated file.
The directory is configured once and then rebuilt incrementally.

Exit codes: 0 when the build and ctest pass, ctest's own code when a test
fails, 124 when ctest runs past the timeout, and 125 when the project does
not configure or build. 125 is git bisect's "cannot test": a mutant that does
not compile is discarded, not killed.

This file imports only the standard library, so it runs under any Python
without the mutator package on its path.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

BUILD_FAILED = 125
TIMED_OUT = 124
NO_PROJECT = 2

_PROJECT = re.compile(r"^\s*project\s*\(", re.IGNORECASE | re.MULTILINE)
_HOME = re.compile(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$", re.MULTILINE)


def build_dir(cwd: Path) -> Path:
    """Where the project in ``cwd`` builds. Relative to ``cwd``, so a worker never shares it."""

    return cwd / "target" / "mutator-build"


def has_project(directory: Path) -> bool:
    path = directory / "CMakeLists.txt"
    if not path.is_file():
        return False
    return bool(_PROJECT.search(path.read_text(encoding="utf-8", errors="replace")))


def _say(message: str) -> None:
    print(f"mutator: {message}", flush=True)


def _configured_for(build: Path, source: Path) -> bool:
    cache = build / "CMakeCache.txt"
    if not cache.is_file():
        return False
    match = _HOME.search(cache.read_text(encoding="utf-8", errors="replace"))
    return match is not None and Path(match.group(1)).resolve() == source.resolve()


def configure_command(source: Path, build: Path) -> list[str]:
    return ["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Debug"]


def build_command(build: Path) -> list[str]:
    return ["cmake", "--build", str(build), "--config", "Debug", "--parallel"]


def ctest_command(timeout: float | None) -> list[str]:
    command = ["ctest", "--output-on-failure", "--no-tests=error", "-C", "Debug"]
    if timeout is not None:
        command.extend(["--timeout", str(max(1, int(timeout + 0.999)))])
    return command


def _run(command: list[str], cwd: Path, timeout: float | None = None) -> int:
    print("+ " + " ".join(command), flush=True)
    try:
        process = subprocess.Popen(command, cwd=cwd, start_new_session=True)
    except OSError as exc:
        _say(f"{command[0]} could not start: {exc}")
        return 127
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        _say(f"ctest ran past {timeout:.1f}s")
        return TIMED_OUT


def _timeout(argv: list[str]) -> float | None:
    if "--test-timeout" not in argv:
        return None
    index = argv.index("--test-timeout")
    return float(argv[index + 1])


def run(cwd: Path, timeout: float | None) -> int:
    if not has_project(cwd):
        _say(f"no CMakeLists.txt with project() in {cwd} or above the mutated file")
        return NO_PROJECT
    build = build_dir(cwd)
    if not _configured_for(build, cwd):
        shutil.rmtree(build, ignore_errors=True)
        build.mkdir(parents=True, exist_ok=True)
        if _run(configure_command(cwd, build), build) != 0:
            _say("cmake could not configure the project")
            return BUILD_FAILED
    if _run(build_command(build), build) != 0:
        _say("the build failed")
        return BUILD_FAILED
    return _run(ctest_command(timeout), build, timeout)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return run(Path.cwd(), _timeout(args))


if __name__ == "__main__":
    sys.exit(main())
