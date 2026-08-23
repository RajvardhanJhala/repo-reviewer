"""The fiddly part GitHub punishes you for: exact new-file line mapping."""
import textwrap

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
