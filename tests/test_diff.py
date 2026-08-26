"""The fiddly part GitHub punishes you for: exact new-file line mapping."""
import os
import textwrap
from pathlib import Path

from review.diff import commentable_lines, parse_diff

MULTI_HUNK = textwrap.dedent("""\
    --- a/app.py
    +++ b/app.py
    @@ -1,3 +1,4 @@
     import os
    +import sys
     import json
     import re
    @@ -20,4 +21,3 @@ def handler():
         x = 1
    -    y = legacy()
    -    z = old()
    +    y = modern()
         return y
    """)


def test_added_lines_get_exact_new_file_numbers():
    files = parse_diff(MULTI_HUNK)
    h1, h2 = files[0].hunks
    assert h1.added == [(2, "import sys")]
    # New-file numbers come from the hunk header (+21,3), not old-file counting:
    # new 21 = "x = 1", new 22 = the replacement line, new 23 = "return y".
    assert h2.added == [(22, "    y = modern()")]


def test_removed_lines_keep_old_numbers_and_no_new_number():
    files = parse_diff(MULTI_HUNK)
    removed = files[0].hunks[1].removed
    assert removed == [(21, "    y = legacy()"), (22, "    z = old()")]


def test_hunk_bounds_cover_new_file_region():
    h1, h2 = parse_diff(MULTI_HUNK)[0].hunks
    assert (h1.new_start, h1.new_end) == (1, 4)
    assert (h2.new_start, h2.new_end) == (21, 23)


def test_commentable_lines_are_plus_and_context_only():
    ok = commentable_lines(parse_diff(MULTI_HUNK))
    assert ok["app.py"] == {1, 2, 3, 4, 21, 22, 23}   # removed lines have no new number


def test_new_and_deleted_files():
    diff = textwrap.dedent("""\
        --- /dev/null
        +++ b/fresh.py
        @@ -0,0 +1,2 @@
        +a = 1
        +b = 2
        --- a/gone.py
        +++ /dev/null
        @@ -1,2 +0,0 @@
        -a = 1
        -b = 2
        """)
    files = parse_diff(diff)
    by_path = {f.path: f for f in files}
    assert by_path["fresh.py"].is_new and by_path["fresh.py"].added_line_count == 2
    assert by_path["gone.py"].is_deleted
    assert "gone.py" not in commentable_lines(files)   # nothing to anchor to


def test_lockfiles_and_binaries_skipped():
    diff = textwrap.dedent("""\
        --- a/package-lock.json
        +++ b/package-lock.json
        @@ -1,1 +1,1 @@
        -x
        +y
        """)
    assert parse_diff(diff) == []


def test_render_tags_lines_with_new_numbers():
    text = parse_diff(MULTI_HUNK)[0].hunks[1].render()
    assert "   22 +     y = modern()" in text
    assert "    - -     y = legacy()" in text


# --- regression: PyGithub patches lack file headers (found by the first live PR run)

class FakeGHFile:
    """Mimics PyGithub's File: .patch holds ONLY hunks, no ---/+++ headers."""

    def __init__(self, filename, patch, status="modified", previous_filename=None):
        self.filename = filename
        self.patch = patch
        self.status = status
        self.previous_filename = previous_filename


def test_build_diff_adds_file_headers_so_the_parser_accepts_it():
    from gh.client import build_diff
    # 2 lines each side: 1 changed + 1 context. The counts in @@ must match exactly.
    hunk = "@@ -18,2 +18,2 @@ def f():\n-    return a, b\n+    return {'a': a}\n     pass"
    diff = build_diff([FakeGHFile("app/search.py", hunk)])
    assert diff.startswith("--- a/app/search.py\n+++ b/app/search.py\n@@")
    files = parse_diff(diff)                      # would raise UnidiffParseError before the fix
    assert files[0].path == "app/search.py"


def test_build_diff_marks_added_and_removed_with_dev_null():
    from gh.client import build_diff
    added = FakeGHFile("new.py", "@@ -0,0 +1,1 @@\n+x = 1", status="added")
    removed = FakeGHFile("old.py", "@@ -1,1 +0,0 @@\n-x = 1", status="removed")
    files = {f.path: f for f in parse_diff(build_diff([added, removed]))}
    assert files["new.py"].is_new
    assert files["old.py"].is_deleted


def test_build_diff_uses_previous_filename_for_renames():
    from gh.client import build_diff
    renamed = FakeGHFile("new_name.py", "@@ -1,1 +1,1 @@\n-a\n+b",
                         status="renamed", previous_filename="old_name.py")
    diff = build_diff([renamed])
    assert "--- a/old_name.py" in diff and "+++ b/new_name.py" in diff


def test_build_diff_skips_binary_files():
    from gh.client import build_diff
    assert build_diff([FakeGHFile("logo.png", None)]) == ""


def test_cli_survives_a_repo_with_its_own_config_module(tmp_path):
    """Regression: the reviewed repo must never shadow our modules.

    A repo with its own config.py at the root (extremely common — Flask, Django,
    countless others) broke the first real GitHub Action run with
    `ImportError: cannot import name 'settings' from 'config'`, because Python puts
    the working directory first on sys.path. The reviewer must run from its own
    directory and treat the reviewed repo purely as data.
    """
    import subprocess
    import sys

    (tmp_path / "config.py").write_text("DEBUG = True\n", encoding="utf-8")   # the decoy
    repo_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(repo_root)}

    def import_settings_from(cwd):
        # PYTHONPATH is identical in both runs, so cwd is the only variable.
        return subprocess.run([sys.executable, "-c", "from config import settings"],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=120)

    # The bug: cwd inside the reviewed repo puts its config.py first on sys.path.
    broken = import_settings_from(tmp_path)
    assert broken.returncode != 0
    assert "cannot import name 'settings'" in broken.stderr

    # The fix: run from our own directory; the reviewed repo is data, not cwd.
    fixed = import_settings_from(repo_root)
    assert fixed.returncode == 0, fixed.stderr

    # And the CLI itself still starts from there.
    cli = subprocess.run([sys.executable, "-m", "review", "--help"],
                         cwd=repo_root, env=env, capture_output=True, text=True, timeout=120)
    assert cli.returncode == 0 and "--pr" in cli.stdout
