"""A missing grammar under QUALITY_TOOLS_OFFLINE ends the run by name."""

import pytest

from mutator import cli
from mutator.crapper_link import ensure_crapper


def test_a_missing_grammar_exits_1_with_its_name(monkeypatch, capsys):
    crapper = ensure_crapper()

    def missing(_argv):
        raise crapper.languages.treesitter.GrammarUnavailable("tree-sitter grammar 'c' is not in /x")

    monkeypatch.setattr(cli, "run", missing)
    with pytest.raises(SystemExit) as exit_:
        cli.main([])
    assert exit_.value.code == 1
    assert "grammar 'c'" in capsys.readouterr().err


def test_other_runtime_errors_still_raise(monkeypatch):
    def broken(_argv):
        raise RuntimeError("unrelated")

    monkeypatch.setattr(cli, "run", broken)
    with pytest.raises(RuntimeError, match="unrelated"):
        cli.main([])
