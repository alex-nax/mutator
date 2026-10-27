# mutator

Mutation testing for Clojure, Java, Go, TypeScript, Rust, Python, C, and C++. One run detects the language of each source file, applies that language's mutation rules, and writes the snapshot [uml-viewer](https://github.com/unclebob/uml-viewer) already reads.

Clojure follows [clj-mutate](https://github.com/unclebob/clj-mutate). Go follows [mutate4go](https://github.com/unclebob/mutate4go). Java follows [mutate4java](https://github.com/unclebob/mutate4java). TypeScript, Rust, Python, C, and C++ use the same decisions as Java, spelled in that language; C and C++ add the operators Java leaves out. Function names and namespaces come from [crapper](https://github.com/unclebob/crapper), which is what uml-viewer joins to `.metrics/crap.edn`.

A mutant is killed when the tests fail or time out. It survives when the tests still pass. A C or C++ mutant that does not compile is discarded: it is neither killed nor survived. A site on a line the coverage report does not hit is uncovered and is not run. The score uml-viewer paints is killed / (killed + survived), so a discarded mutant does not move it.

## Run

```bash
./mutator
```

The first run creates `.venv` and installs the tool. crapper has to be checked out next to this directory (`../crapper`), because that is where the namespace and function names come from. From a project root the tool walks the tree, skips `test`, `spec`, `vendor`, `node_modules`, and `target`, and writes `.metrics/mutate/<namespace>.edn`.

The first run of a tree executes every covered mutant. Start with the file you are working on.

```bash
./mutator src/demo/core.clj          # one file, or a directory
./mutator --scan src/demo           # list sites, do not run tests
./mutator --changed                 # git additions and edits
./mutator --mutate-all              # rerun killed mutants too
./mutator --no-coverage             # do not skip uncovered lines
./mutator --use-existing-coverage   # read reports already on disk
./mutator --test-command "pytest -q tests/test_demo.py"
./mutator --max-workers 3 src/demo/core.clj
```

Once a snapshot exists, the next run is differential. It reruns survivors and every site in a function whose text changed. Killed mutants in an unchanged function stay killed. `--since-last-run` is that same selection. `--mutate-all` ignores it.

Exit `0` when every executed mutant was killed or discarded. Exit `2` when the baseline tests fail, or when a C/C++ worker cannot build the unmutated project. Exit `3` when a mutant survives. A failed baseline does not rewrite the snapshot.

## Workers

Mutants of one file run at the same time. The default is one worker per core. `--max-workers` sets the cap. The run uses the smaller of that cap, the number of cores, and the number of selected sites. Files are still taken one at a time.

Each worker is a directory under `target/mutation-workers`. The file being mutated is a private copy. A module that reaches that file through a relative import is copied too, so Node resolves the import to the private copy. The rest of the tree is linked. The original files stay as they are. The overlay is removed when that file's mutants finish.

A C or C++ worker builds into `target/mutator-build` under its own copy of the CMake project directory, so no two workers share a build directory and each compiles its own copy of the mutated file. Each worker is configured and built once, unmutated and without a timeout, before its first mutant. If that build fails (for example because the project compiles from a `build/` directory, which a worker does not link), the file stops with exit `2` instead of discarding every mutant. After that, each mutant costs an incremental build (the mutated translation unit and a relink) plus `ctest`. A worker's build directory lives inside the overlay and is removed with it, so every file pays one full build per worker.

The baseline still runs once, in the real tree, before any worker starts. The mutant command then runs inside the worker, in the same directory it would have used in the real tree, so a relative path such as `src` or `./pkg` refers to the overlay. A copy left under `target/mutator-backup/` by an interrupted older run is restored before a non-scan run.

## Snapshot

uml-viewer loads every `*.edn` file under `.metrics/mutate`. Each file is one namespace. A Java file with an inner class, or a Go file with a function and a method, therefore writes more than one file. Two Go files in the same package share one snapshot, and rerunning one of them keeps the other's forms.

```clojure
{:version 1
 :source "src/demo/board.py"
 :namespace "demo.board.Board"
 :outcomes {"[\"src/demo/board.py\",\"demo.board.Board\",\"defn/place\",10,11,\">\",\">=\"]" :survived}
 :forms [{:id "defn/place"
          :kind "defn"
          :file "src/demo/board.py"
          :line 2
          :end-line 4
          :hash "…"
          :killed 1
          :survived 1
          :uncovered 0
          :sites 2}
         {:id "defn-/_hide"
          :kind "defn-"
          :killed 0
          :survived 0
          :uncovered 1
          :sites 1}]}
```

`:namespace` is the class key crapper uses. The form id is what the viewer splits:

| Form id | Operation | Private |
| --- | --- | --- |
| `defn/place` | `place` | no |
| `defn-/hide` | `hide` | yes |

`:sites` is every mutation in the function, including uncovered ones. The viewer prints `---no mutation sites---` when that count is zero.

| Language | `:namespace` | `:name` |
| --- | --- | --- |
| Clojure | the `ns` | the `defn` / `defn-` name |
| Java | `package.Class`, or `package.Outer.Inner` | the method name |
| Go | the package import path, or `import/path.Receiver` | the function or method name |
| TypeScript | the dotted module path, or `module.Class` | the function or method name |
| Rust | `crate::module`, or `crate::module::Type` | the function or method name |
| Python | the dotted module path, or `module.Class` | the function or method name |

| C | the file's project-relative path without its extension (`src/calc`) | the function name |
| C++ | `ns::Class`, or the file's path for a global function | the function or method name; an overload adds its parameter types |

`defn-` is Clojure's private form. Elsewhere a private method, an unexported Go function, a Python name that starts with `_`, a Rust function that is not `pub`, a TypeScript `private` / `#` name, or a `static` C function uses `defn-/`. C++ access is not read, so every C++ form is `defn/`.

A form with a C or C++ mutant that did not compile also carries `:discarded`, and its outcome is `:discarded`. A discarded mutant is not rebuilt while its function's text is unchanged.

## What each language mutates

Tokens inside strings, comments, and quoted Clojure forms are left alone. In every language but C and C++, a replacement that does not compile fails the test run and counts as killed. A C or C++ replacement that does not compile is discarded and reported as `DISCARDED`.

| Language | Mutations |
| --- | --- |
| Clojure | clj-mutate's head symbols: `+`/`-`, `*` to `/`, `inc`/`dec`, comparisons, `=`/`not=`, `if`/`if-not`, `when`/`when-not`, `and`/`or`, and the seq, predicate, and coercion pairs. `true`/`false` and `0`/`1` anywhere. |
| Java | `true`/`false`, `==`/`!=`, `<`/`<=`, `>`/`>=`, `+`/`-`, `*`/`/`, `&&`/`||`, delete `!` and unary `-`, `0`/`1`. Constructors are not entries, matching crapper. |
| Go | mutate4go: `+`/`-`, `*` to `/` only, comparisons, `==`/`!=`, `true`/`false`, `&&`/`||`, `0`/`1`. |
| TypeScript | the Java set, plus `===`/`!==`, `??` to `||`, and `?.` to `.`. A call or index drops `?.` (`a?.()` becomes `a()`). `.js`, `.jsx`, `.mjs`, and `.cjs` use these rules. |
| Rust | the Java set (`&&`/`||`, `!`, unary `-`). |
| Python | the Java set, spelled `and`/`or`, `not`, and `True`/`False`. |
| C, C++ | the Java set, plus `%` to `*`, `&`/`|`, `^` to `&`, `<<`/`>>`, delete `~`, `++`/`--`, and `return x;` to `return 0;` (not when `x` is already zero, `NULL`, `nullptr`, `false`, `1`, or `true`). C++ `and`/`or`/`not` are mutated too. The condition of an `#if` / `#elif` and the body of a `#define` are left alone; code inside an `#if` branch is mutated as written. `.h` files are C. |

## Tests and coverage

The baseline command has to pass before any mutant runs. A mutant's timeout is ten times that baseline, and at least two seconds. For C and C++ the timeout applies to `ctest` only; the build is not timed, and a test that runs past it kills the mutant. `--timeout-factor` changes the multiple. `--test-command` replaces the default. Defaults:

| Language | Command | Where |
| --- | --- | --- |
| Clojure | `clj -M:spec --tag ~no-mutate` when Speclj is on the classpath, otherwise `clj -M:test` or `bb spec` | nearest `deps.edn` or `bb.edn` |
| Java | `mvn -q test -DexcludeTags=no-mutate` | nearest `pom.xml` |
| Go | `go test -count=1` of the file's package | nearest `go.mod` |
| TypeScript | `npm test` | nearest `package.json` |
| Rust | `cargo test` | nearest `Cargo.toml` |
| Python | the project's `.venv` or `venv` Python running `pytest`, or `unittest discover` | nearest project file |
| C, C++ | `cmake -S . -B target/mutator-build` once, then `cmake --build` and `ctest` | nearest `CMakeLists.txt` that calls `project(` |

C and C++ coverage is crapper's: the CMake project is built instrumented under `target/crapper-build`, and its `ctest` run is exported as LCOV under `target/coverage/c`. Coverage is generated with crapper's commands unless `--use-existing-coverage` or `--no-coverage` is set. A file missing from the report is treated as uncovered, and the run says so.

## C and C++ limits

- Each mutant is a rebuild. A mutant in a header rebuilds every translation unit that includes it.
- The build has no timeout. A mutant that sends the compiler into a loop (a recursive template or `constexpr`) hangs the worker.
- Make 3.81, which macOS ships, compares whole seconds. A mutant is therefore written at least one second after its worker's previous build finished, or the build would be skipped and `ctest` would run the previous binary. A mutant that builds and tests in under a second still waits out the rest of that second.
- `<<` on a C++ stream becomes `>>` and does not compile, and so is discarded. A `++`/`--` swap in a loop usually runs until the timeout.
- Workers build in parallel and each build uses `--parallel`, so a large project can oversubscribe the cores. Lower `--max-workers` for a large project.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
