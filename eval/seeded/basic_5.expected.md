# Planted issues in basic_5.patch (ground truth)

| # | line | kind | issue |
|---|---|---|---|
| 1 | 7 | bug | `range(1, len(items))` skips the first item (off-by-one) |
| 2 | 9 | bug | `total / len(items)` raises ZeroDivisionError on empty list |
| 3 | 10 | security | `os.system("copy ..." + out_path)` — command injection via out_path |
| 4 | 5 | style | `exportReport` camelCase; this repo is snake_case throughout (+ no docstring) |
| 5 | 2 | style | `import subprocess` unused (repo is ruff-clean, F401 would fail CI) |

Milestone bar: >=4 of 5 found, positions exact, <10 total comments.
