"""The report's Scope section (§6.1) must say what produced the findings.

A UX audit that cannot say which model and which prompts generated it is not
reproducible, and two runs a month apart are not comparable. The manifest is the
only place that knows, so it has to record it at run time — this cannot be
back-filled, which is why runs made before this exists will honestly show
`null` rather than a guess.
"""
from src.simulated_web_agent.agent.prompt_registry import PromptRegistry


def _reg(tmp_path, **prompts):
    d = tmp_path / "prompts"
    d.mkdir(parents=True)
    for name, body in prompts.items():
        (d / f"{name}.txt").write_text(body, encoding="utf-8")
    return PromptRegistry(d)


def test_hash_is_stable_for_identical_prompts(tmp_path):
    a = _reg(tmp_path / "a", first_impression="score it", planning="plan it")
    b = _reg(tmp_path / "b", first_impression="score it", planning="plan it")
    assert a.version_hash() == b.version_hash()


def test_hash_changes_when_a_prompt_changes(tmp_path):
    a = _reg(tmp_path / "a", first_impression="score it")
    b = _reg(tmp_path / "b", first_impression="score it AS THE PERSONA")
    assert a.version_hash() != b.version_hash()


def test_hash_changes_when_a_prompt_is_added(tmp_path):
    a = _reg(tmp_path / "a", first_impression="score it")
    b = _reg(tmp_path / "b", first_impression="score it", bias_audit="audit it")
    assert a.version_hash() != b.version_hash()


def test_hash_ignores_file_order(tmp_path):
    """Two checkouts must agree regardless of how the filesystem lists files."""
    a = _reg(tmp_path / "a", planning="p", action="a")
    b = _reg(tmp_path / "b", action="a", planning="p")
    assert a.version_hash() == b.version_hash()


def test_hash_is_short_enough_to_read(tmp_path):
    h = _reg(tmp_path / "a", planning="p").version_hash()
    assert len(h) == 12
    assert h.isalnum()


def test_no_prompts_still_yields_a_hash(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert PromptRegistry(d).version_hash()
