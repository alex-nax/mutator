"""Text reports for a mutation run and for --scan."""

from __future__ import annotations

from mutator.model import FormResult, Site


def _score(form: FormResult) -> str:
    if form.score is None:
        return "   n/a"
    return f"{form.score * 100:5.1f}%"


def _row(name: str, namespace: str, counts: tuple[int, ...], score: str, widths, discards: bool) -> str:
    name_width, namespace_width = widths
    killed, survived, uncovered, discarded, sites = counts
    middle = f"{killed:7d} {survived:9d} {uncovered:10d}"
    if discards:
        middle += f" {discarded:10d}"
    return f"{name:<{name_width}} {namespace:<{namespace_width}} {middle} {sites:6d} {score}"


def format_results(forms: list[FormResult]) -> str:
    """The per-function table. A Discarded column appears only when a C/C++ mutant did not build."""

    if not forms:
        return "No functions to mutate.\n"
    discards = any(form.discarded for form in forms)
    name_width = max(8, min(48, max(len(form.name) for form in forms)))
    namespace_width = max(9, min(72, max(len(form.namespace) for form in forms)))
    widths = (name_width, namespace_width)
    header = (
        f"{'Function':<{name_width}} {'Namespace':<{namespace_width}} "
        f"{'Killed':>7} {'Survived':>9} {'Uncovered':>10}"
        + (f" {'Discarded':>10}" if discards else "")
        + f" {'Sites':>6} {'Score':>7}"
    )
    ordered = sorted(
        forms,
        key=lambda form: (form.score is not None, form.score or 0, -form.survived, form.name),
    )
    lines = [header, "-" * len(header)]
    totals = [0, 0, 0, 0, 0]
    for form in ordered:
        counts = (form.killed, form.survived, form.uncovered, form.discarded, form.sites)
        lines.append(_row(form.name, form.namespace, counts, _score(form), widths, discards))
        totals = [total + count for total, count in zip(totals, counts)]
    killed, survived = totals[0], totals[1]
    executed = killed + survived
    score = "n/a" if executed == 0 else f"{(killed / executed) * 100:.1f}%"
    lines.append("-" * len(header))
    lines.append(_row("Total", "", tuple(totals), f"{score:>7}", widths, discards))
    lines.append("")
    return "\n".join(lines)


def format_site_log(sites: list[Site], status: dict[str, str]) -> str:
    lines = []
    for site in sites:
        label = status.get(site.mutation_id)
        if label is None:
            continue
        lines.append(f"{label.upper():9} {site.file}:{site.line} {site.description}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def format_scan(
    path: str, sites: list[Site], changed: set[tuple[str, str]], covered: dict[str, bool] | None
) -> str:
    lines = [f"Scan: {len(sites)} mutation sites in {path}"]
    for site in sites:
        mark = "*" if (site.namespace, site.form_id) in changed else " "
        coverage = ""
        if covered is not None and not covered.get(site.mutation_id, True):
            coverage = " uncovered"
        lines.append(f"{mark} {path}:{site.line} {site.description}{coverage}  [{site.form_id}]")
    if changed:
        lines.append("* marks a function whose text changed since the last snapshot.")
    lines.append("")
    return "\n".join(lines)
