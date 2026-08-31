"""Markdown structure check for the documents we publish.

Written after a multi-line blockquote was inserted into the middle of a table in
`generator-spec.md`, orphaning six rows so GitHub rendered them as literal text
with visible pipes. The check that existed then looked for a *single* blockquote
line with table rows immediately above and below, so a note spanning twelve
lines went straight past it.

The lesson was not "handle twelve lines too". It was that a hand-written
heuristic about adjacent lines is guessing at what a parser does. So the primary
check here **renders the document** with a CommonMark parser and looks at the
result: a table row that failed to parse comes out as a paragraph still
containing its pipe characters, which is exactly what a reader would see.

    python -m pipeline.mdcheck            every tracked markdown file
    python -m pipeline.mdcheck FILE ...   just these

Exits non-zero if anything is found.

Two things deliberately NOT reported, both confirmed against the parser rather
than argued about:

  - Lazy continuation. A list item wrapped onto an indented next line is one
    item, not a split list.
  - An ordered list interrupted by a block and resuming at the right number.
    CommonMark emits `<ol start="3">`, so the numbering a reader sees is
    correct. Only a resume at the wrong number is reported.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ESCAPED_PIPE = re.compile(r"\\\|")
_SEPARATOR = re.compile(r"^\|[\s:\-|]+\|\s*$")
_ORDERED = re.compile(r"^(\d+)[.)]\s+\S")


def _parser():
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        return None
    return MarkdownIt("commonmark").enable("table")


def _rendered_problems(md, lines):
    """Render, then look for table rows that came out as prose.

    A row GitHub failed to parse keeps its pipes and lands inside a paragraph.
    Nothing else in these documents puts two or more unescaped pipes in running
    text, so this is specific as well as direct.
    """
    problems = []
    src = "\n".join(lines)
    tokens = md.parse(src)
    for i, t in enumerate(tokens):
        if t.type != "inline":
            continue
        parent = tokens[i - 1].type if i else ""
        if parent != "paragraph_open":
            continue
        # Count pipes in TEXT children only. A pipe inside inline code, as in
        # `max |diff| = 0`, is prose and not a broken table row. Walking the
        # children is exact where a regex over the raw source is not.
        body = "".join(c.content for c in (t.children or []) if c.type == "text")
        body = _ESCAPED_PIPE.sub("", body)
        if body.count("|") >= 2:
            line = (t.map[0] + 1) if t.map else (
                tokens[i - 1].map[0] + 1 if tokens[i - 1].map else 0)
            problems.append(
                "line %d renders as a PARAGRAPH but contains %d pipes, so a "
                "table row is being shown as literal text: %s"
                % (line, body.count("|"), body[:60].strip()))
    return problems


def _static_problems(lines):
    """Checks a renderer will not make for us."""
    problems = []
    if sum(1 for l in lines if l.strip().startswith("```")) % 2:
        problems.append("unbalanced ``` fences")

    # table shape
    i = 0
    in_fence = False
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or not lines[i].startswith("|"):
            i += 1
            continue
        j = i
        while j + 1 < len(lines) and lines[j + 1].startswith("|"):
            j += 1
        body = lines[i:j + 1]
        if len(body) >= 2 and not _SEPARATOR.match(body[1]):
            problems.append("table at line %d has no separator row" % (i + 1))
        widths = {_ESCAPED_PIPE.sub("", l).count("|")
                  for l in body if not _SEPARATOR.match(l)}
        if len(widths) > 1:
            problems.append("table at line %d has ragged columns %s"
                            % (i + 1, sorted(widths)))
        i = j + 1

    # an ordered list resuming at the wrong number after an interruption
    last_num = None
    for n, l in enumerate(lines):
        m = _ORDERED.match(l)
        if not m:
            if l.strip() and not l.startswith((" ", "\t")) and last_num is not None:
                pass          # an interruption; the next number is what matters
            continue
        num = int(m.group(1))
        if last_num is not None and num != last_num + 1 and num != 1:
            problems.append("ordered list at line %d resumes at %d after %d, so "
                            "the visible numbering skips" % (n + 1, num, last_num))
        last_num = num if num != 1 or last_num is None else num
        if num == 1:
            last_num = 1
    return problems


def check_file(path, md=None):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    problems = _static_problems(lines)
    if md is not None:
        problems = _rendered_problems(md, lines) + problems
    return problems


def tracked_markdown():
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    # Templates are inputs, not documents. Their placeholders carry a pipe
    # as a format separator, and it is the rendered output a reader sees,
    # which is checked in its own right.
    return [os.path.join(ROOT, p) for p in out
            if not p.endswith('.template.md')]


def main(argv):
    paths = argv or tracked_markdown()
    md = _parser()
    if md is None:
        print("  markdown_it not installed: running static checks only, which")
        print("  will not catch a table that fails to parse.")
    bad = 0
    for p in sorted(paths):
        probs = check_file(p, md)
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if probs:
            bad += 1
            print("  %s" % rel)
            for x in probs:
                print("      %s" % x)
        else:
            print("  %-36s OK" % rel)
    print()
    print("%d file(s) with problems, %d checked" % (bad, len(paths)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
