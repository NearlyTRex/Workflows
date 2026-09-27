import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "check_markdown_fences", ROOT / "actions" / "check-markdown-fences" / "check_markdown_fences.py")
check_markdown_fences = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_markdown_fences)
find_problems = check_markdown_fences.find_problems


def repo_with(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)


def test_well_formed_fences_pass():
    text = "# Doc\n\n```bash\necho hi\n```\n\nText\n\n~~~\nplain\n~~~\n"
    assert find_problems(text) == []


def test_text_after_a_closing_fence_is_reported():
    # The block runs on past this line, hiding what follows from the lint rules
    text = "```bash\npip install x\n``` What\neach command does is here.\n"
    assert find_problems(text) == [(3, "text after a closing fence, so the code block opened on "
                                       "line 1 does not end here: ``` What")]


def test_a_block_that_never_closes_is_reported():
    assert find_problems("Intro\n\n```python\nprint(1)\n") == [(3, "code block is never closed")]


def test_a_longer_outer_fence_can_show_a_fence_inside():
    text = "````markdown\n```bash\necho hi\n```\n````\n"
    assert find_problems(text) == []


def test_a_fence_of_the_other_character_does_not_close():
    text = "~~~\n```\n~~~\n"
    assert find_problems(text) == []


def test_fences_in_list_items_and_quotes_are_followed():
    text = "- Step:\n\n  ```bash\n  run\n  ```\n\n> ```\n> quoted\n> ```\n"
    assert find_problems(text) == []


def test_inline_triple_backticks_do_not_open_a_block():
    assert find_problems("Use ```code``` inline.\n\nMore text.\n") == []


def test_one_mistake_is_reported_once():
    # After a bad closing fence the rest of the file is read as if it had closed
    text = "```\na\n``` oops\n\nText\n\n```bash\nb\n```\n"
    assert [line for line, _ in find_problems(text)] == [3]


def test_tracked_files_are_checked_and_ignores_respected(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {
        "ok.md": "```\nfine\n```\n",
        "docs/bad.md": "```\nnever closed\n",
        "vendor/theirs.md": "```\nnot ours to fix\n",
        ".markdownlintignore": "# Vendored\nvendor/\n",
    })
    (tmp_path / "untracked.md").write_text("```\nnot tracked\n")
    monkeypatch.chdir(tmp_path)

    assert check_markdown_fences.main() == 1
    out = capsys.readouterr().out
    assert "::error file=docs/bad.md,line=1::code block is never closed" in out
    assert "vendor/theirs.md" not in out and "untracked.md" not in out
    assert "2 Markdown files checked, 1 fence problems" in out
