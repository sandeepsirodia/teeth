"""teeth: your tests are green. Do they bite?

Diff-scoped mutation testing. Mutates only the lines your branch changed (flip a `>`, drop a
check, blank a return), reruns your tests once per mutant, and reports every mutant that
survived: a behavior change your tests did not notice. Standard library only.
"""
import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

__version__ = "0.1.0"

LANGS = {".py": "py", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js", ".ts": "js", ".tsx": "js",
         ".go": "go", ".rs": "rs"}
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|"
                     r"\.(test|spec)\.[cm]?[jt]sx?$")
HEAVY_DIRS = {"node_modules", ".venv", "venv", "target", ".tox", "vendor"}  # symlinked, not copied
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}  # never copied: stale caches hide mutants
IGNORE_RE = re.compile(r"teeth:\s*ignore\b\W*(.*)")


# ------------------------------------------------------------------ lexing

def code_mask(text, lang):
    """Return `text` with every string/comment character replaced by \\0 (same length), so mutators
    only ever see real code. Handles # and // and /* */ comments, '', "", `` and Python triple quotes."""
    out, i, n = list(text), 0, len(text)
    line_comment = "#" if lang == "py" else "//"
    while i < n:
        c = text[i]
        if text.startswith(line_comment, i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            out[i:j] = "\0" * (j - i)
            i = j
        elif lang != "py" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out[i:j] = ["\0" if ch != "\n" else "\n" for ch in text[i:j]]
            i = j
        elif lang == "py" and text.startswith(('"""', "'''"), i):
            q = text[i:i + 3]
            j = text.find(q, i + 3)
            j = n if j == -1 else j + 3
            out[i:j] = ["\0" if ch != "\n" else "\n" for ch in text[i:j]]
            i = j
        elif c in "\"'`" and not (lang == "py" and c == "`"):
            if lang == "rs" and c == "'" and not re.match(r"'(\\.|[^'\\])'", text[i:]):
                i += 1  # Rust lifetime ('a), not a char literal
                continue
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and c != "`":
                    break  # unterminated single-line string: stop at end of line
                j += 1
            j = min(n, j + 1)
            out[i:j] = ["\0" if ch != "\n" else "\n" for ch in text[i:j]]
            i = j
        else:
            i += 1
    return "".join(out)


# ------------------------------------------------------------------ mutators

def _sub_ops(lang):
    """(name, regex over masked code, replacement-or-callable)."""
    ops = [
        ("comparison", r"===", "!=="), ("comparison", r"!==", "==="),
        ("comparison", r"(?<![=!<>])==(?!=)", "!="), ("comparison", r"(?<![=!])!=(?!=)", "=="),
        ("comparison", r"(?<![<>=\-])<=", "<"), ("comparison", r"(?<![<>=\-])>=", ">"),
        ("comparison", r"(?<![<>=\-!])<(?![<=\-])", "<="), ("comparison", r"(?<![<>=\-!])>(?![>=])", ">="),
        ("arithmetic", r"(?<=\s)\+(?=\s)", "-"), ("arithmetic", r"(?<=\s)-(?=\s)", "+"),
        ("arithmetic", r"(?<=\s)\*(?=\s)", "/"), ("arithmetic", r"(?<=\s)/(?=\s)", "*"),
        ("constant", r"(?<![\w.])0(?![\w.])", "1"), ("constant", r"(?<![\w.])1(?![\w.])", "0"),
        ("constant", r"(?<![\w.])([2-9]|[1-9]\d+)(?![\w.])", lambda m: str(int(m.group(0)) + 1)),
    ]
    if lang == "py":
        ops += [("boolean", r"\band\b", "or"), ("boolean", r"\bor\b", "and"),
                ("boolean", r"\bTrue\b", "False"), ("boolean", r"\bFalse\b", "True"),
                ("boolean", r"\bnot\s+", "")]
    else:
        ops += [("boolean", r"&&", "||"), ("boolean", r"\|\|", "&&"),
                ("boolean", r"\btrue\b", "false"), ("boolean", r"\bfalse\b", "true")]
    return [(name, re.compile(rx), rep) for name, rx, rep in ops]


def _line_ops(line, masked, lang):
    """Whole-line mutants: returns [(name, new_line)]."""
    indent = re.match(r"\s*", line).group(0)
    masked = masked.replace("\0", " ").rstrip()  # comments/strings become blanks for whole-line patterns
    code = masked.strip()
    out = []
    if lang == "py":
        m = re.match(r"(\s*)return\b\s*(\S.*)$", masked)
        if m and m.group(2).strip() not in ("None",):
            out.append(("return", indent + "return None"))
        if re.match(r"raise\b", code):
            out.append(("delete", indent + "pass"))
        m = re.match(r"(\s*)(el)?if\s+(.+):\s*$", masked)
        if m:
            kw = (m.group(2) or "") + "if"
            out += [("condition", indent + kw + " True:"), ("condition", indent + kw + " False:")]
        if re.match(r"[\w.]+\(.*\)$", code) and _balanced(code):
            out.append(("delete", indent + "pass"))
    else:
        if lang == "js":
            m = re.match(r"return\b\s*(\S.*?);?\s*$", code)
            if m and m.group(1) not in ("null", "undefined", ";"):
                out.append(("return", indent + "return null;"))
        if re.match(r"throw\b", code):
            out.append(("delete", indent + ";" if lang == "js" else indent + "{}"))
        m = re.match(r"(\s*)((?:}\s*else\s+)?if\s*)\((.+)\)\s*(\{?)\s*$", masked)
        if m:
            out += [("condition", indent + m.group(2) + "(true) " + m.group(4)),
                    ("condition", indent + m.group(2) + "(false) " + m.group(4))]
        elif lang in ("go", "rs"):
            m = re.match(r"(\s*)((?:}\s*else\s+)?if\s+)(.+?)\s*\{\s*$", masked)
            if m:
                out += [("condition", indent + m.group(2) + "true {"), ("condition", indent + m.group(2) + "false {")]
        if re.match(r"[\w.]+\(.*\);?$", code) and _balanced(code) and lang == "js":
            out.append(("delete", indent + ";"))
    return [(name, new.rstrip()) for name, new in out]


def _balanced(s):
    depth = 0
    for ch in s:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def mutants_for_file(path, text, lines_changed, lang):
    """Every mutant on the changed lines of one file: dicts with file, line, op, before, after."""
    masked_lines = code_mask(text, lang).split("\n")
    src_lines = text.split("\n")
    ops = _sub_ops(lang)
    found, waived = [], []
    for ln in sorted(lines_changed):
        if ln < 1 or ln > len(src_lines):
            continue
        line, masked = src_lines[ln - 1], masked_lines[ln - 1]
        ig = IGNORE_RE.search(line)
        if ig:
            waived.append({"file": path, "line": ln, "reason": ig.group(1).strip() or "(no reason given)"})
            continue
        if not masked.strip() or not masked.replace("\0", "").strip():
            continue
        candidates = []
        for name, rx, rep in ops:
            for m in rx.finditer(masked):
                new_tok = rep(m) if callable(rep) else rep
                candidates.append((name, line[:m.start()] + new_tok + line[m.end():]))
        candidates += _line_ops(line, masked, lang)
        seen = set()
        for name, new in candidates:
            if new.strip() == line.strip() or new in seen:
                continue  # equivalent after normalization
            seen.add(new)
            found.append({"file": path, "line": ln, "op": name, "before": line.strip(), "after": new.strip(),
                          "new_line": new})
    return found, waived


# ------------------------------------------------------------------ git diff

def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def default_base(repo):
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=repo, capture_output=True).returncode == 0:
            return ref
    return "HEAD~1"


def changed_lines(repo, base):
    """{path: set(new line numbers)} for lines added/changed vs merge-base(base, HEAD), incl. uncommitted."""
    mb = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=repo, capture_output=True, text=True)
    ref = mb.stdout.strip() if mb.returncode == 0 else base
    out, path = {}, None
    for raw in git(repo, "diff", "-U0", "--no-color", "--no-renames", ref).splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            path = None if p == "/dev/null" else p[2:] if p.startswith("b/") else p
        elif raw.startswith("@@") and path:
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))?", raw)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(path, set()).update(range(start, start + count))
    for p in git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        try:
            with open(os.path.join(repo, p), encoding="utf-8") as f:
                out[p] = set(range(1, f.read().count("\n") + 2))
        except (OSError, UnicodeDecodeError):
            pass
    return {p: ls for p, ls in out.items() if os.path.splitext(p)[1] in LANGS and not TEST_RE.search(p)}


def collect(repo, base):
    mutants, waived = [], []
    for path, ls in sorted(changed_lines(repo, base).items()):
        full = os.path.join(repo, path)
        if not os.path.isfile(full):
            continue
        with open(full, encoding="utf-8") as f:
            text = f.read()
        m, w = mutants_for_file(path, text, ls, LANGS[os.path.splitext(path)[1]])
        mutants += m
        waived += w
    return mutants, waived


def sample(mutants, k, seed=0):
    """Pick up to k mutants round-robin across files (so one big file can't eat the budget)."""
    if k is None or len(mutants) <= k:
        return mutants
    rng = random.Random(seed)
    by_file = {}
    for m in mutants:
        by_file.setdefault(m["file"], []).append(m)
    for ms in by_file.values():
        rng.shuffle(ms)
    picked, files = [], sorted(by_file)
    while len(picked) < k:
        for f in files:
            if by_file[f] and len(picked) < k:
                picked.append(by_file[f].pop())
    return sorted(picked, key=lambda m: (m["file"], m["line"], m["after"]))


# ------------------------------------------------------------------ running

def make_copy(repo):
    d = tempfile.mkdtemp(prefix="teeth-")

    def ignore(src, names):
        return [n for n in names if n == ".git" or n in CACHE_DIRS or n.endswith(".pyc")
                or (n in HEAVY_DIRS and os.path.abspath(src) == repo)]

    shutil.copytree(repo, d, dirs_exist_ok=True, ignore=ignore, symlinks=True)
    for h in HEAVY_DIRS:
        if os.path.isdir(os.path.join(repo, h)):
            os.symlink(os.path.join(repo, h), os.path.join(d, h))
    return d


def write_touch(path, text):
    """Write and move mtime forward, so any mtime-based cache (pyc, jest, tsc…) sees a change."""
    before = os.stat(path).st_mtime
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    bump = max(before, os.stat(path).st_mtime) + 2
    os.utime(path, (bump, bump))


def run_tests(cmd, cwd, timeout):
    # No bytecode cache: a same-size mutant written within the same second as the original would
    # otherwise run the cached original bytecode, and the mutant would silently never execute.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    try:
        return "passed" if p.wait(timeout=timeout) == 0 else "failed"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()
        return "timeout"


def evaluate(repo, mutants, test_cmd, jobs=1, timeout=None):
    """Run the tests once per mutant, each worker in its own copy of the repo. Returns results in input order."""
    copies = [make_copy(repo) for _ in range(max(1, jobs))]
    try:
        if run_tests(test_cmd, copies[0], timeout) != "passed":
            raise SystemExit("teeth: your tests fail (or time out) without any mutation. Fix them first.")
        free = list(copies)
        lock = threading.Lock()

        def one(m):
            with lock:
                d = free.pop()
            path = os.path.join(d, m["file"])
            try:
                with open(path, encoding="utf-8") as f:
                    original = f.read()
                lines = original.split("\n")
                lines[m["line"] - 1] = m["new_line"]
                write_touch(path, "\n".join(lines))
                cmd = test_cmd.replace("{file}", m["file"])
                outcome = run_tests(cmd, d, timeout)
                write_touch(path, original)
                return {"passed": "survived", "failed": "killed", "timeout": "timeout"}[outcome]
            finally:
                with lock:
                    free.append(d)

        with ThreadPoolExecutor(max_workers=len(copies)) as pool:
            outcomes = list(pool.map(one, mutants))
    finally:
        for d in copies:
            shutil.rmtree(d, ignore_errors=True)
    return [dict(m, status=s) for m, s in zip(mutants, outcomes)]


def score(results):
    total = len(results)
    caught = sum(r["status"] in ("killed", "timeout") for r in results)
    return (caught / total) if total else None


# ------------------------------------------------------------------ output

def report(results, waived, out, fmt="text"):
    s = score(results)
    survivors = [r for r in results if r["status"] == "survived"]
    if fmt == "github":
        for r in survivors:
            out.write("::warning file=%s,line=%d::teeth: mutant survived (%s): %s  →  %s\n" % (
                r["file"], r["line"], r["op"], r["before"], r["after"]))
    if s is None:
        out.write("No mutable code in the changed lines.\n")
        return
    killed = sum(r["status"] == "killed" for r in results)
    timeouts = sum(r["status"] == "timeout" for r in results)
    out.write("Mutation score: %d/%d caught (%.0f%%)%s\n" % (
        killed + timeouts, len(results), 100 * s, " — %d by timeout" % timeouts if timeouts else ""))
    if survivors:
        out.write("\n%d mutant(s) survived. Your tests did not notice these behavior changes:\n" % len(survivors))
        cur = None
        for r in survivors:
            if r["file"] != cur:
                cur = r["file"]
                out.write("\n  %s\n" % cur)
            out.write("    :%-4d %-10s %s\n               → %s\n" % (r["line"], r["op"], r["before"], r["after"]))
    if waived:
        out.write("\nWaived (teeth: ignore):\n")
        for w in waived:
            out.write("  %s:%d  %s\n" % (w["file"], w["line"], w["reason"]))


def hook(args, stdin):
    """Claude Code Stop hook: block 'done' while mutants survive on the changed lines."""
    try:
        data = json.load(stdin)
    except ValueError:
        data = {}
    if data.get("stop_hook_active"):
        return 0  # we already blocked once this turn; don't loop forever
    repo = os.path.abspath(data.get("cwd") or ".")
    try:
        mutants, _ = collect(repo, args.base or default_base(repo))
        results = evaluate(repo, sample(mutants, args.max_mutants), args.test_cmd, args.jobs, args.timeout)
    except (SystemExit, subprocess.CalledProcessError, OSError):
        return 0  # never break the agent because of teeth itself
    s = score(results)
    if s is None or s >= args.min_score:
        return 0
    lines = ["%s:%d  %s  →  %s" % (r["file"], r["line"], r["before"], r["after"])
             for r in results if r["status"] == "survived"][:10]
    reason = ("teeth: %.0f%% of mutations on your changed lines were caught (need %.0f%%). "
              "Your tests did not notice these changes; add assertions that would fail:\n%s") % (
        100 * s, 100 * args.min_score, "\n".join(lines))
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    return 0


def install_hook(args, out):
    path = args.settings or os.path.join(".claude", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    import shlex
    command = "teeth hook --min-score %s --max-mutants %d --test-cmd %s" % (
        args.min_score, args.max_mutants, shlex.quote(args.test_cmd))
    groups = settings.setdefault("hooks", {}).setdefault("Stop", [])
    groups[:] = [g for g in groups if not any(h.get("command", "").startswith("teeth hook") for h in g.get("hooks", []))]
    groups.append({"hooks": [{"type": "command", "command": command, "timeout": 600}]})
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    out.write("Installed teeth Stop hook in %s\n" % path)
    return 0


# ------------------------------------------------------------------ CLI

def main(argv=None, out=None, stdin=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="teeth", description="Diff-scoped mutation testing: do your tests bite?")
    ap.add_argument("test_cmd", nargs="?", help="command that runs your tests (exit 0 = pass). {file} = mutated file")
    ap.add_argument("--base", help="base ref (default: origin/HEAD, main or master)")
    ap.add_argument("--repo", default=".")
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=300, help="seconds per test run (default 300)")
    ap.add_argument("--max-mutants", type=int, help="sample at most N mutants, spread across files")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-score", type=float, help="exit 1 if the score is below this (0-1)")
    ap.add_argument("--format", choices=["text", "github"], default="text")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="only list the mutants, don't run tests")
    ap.add_argument("--version", action="version", version=__version__)
    ap.epilog = "Also: teeth hook --test-cmd CMD (Claude Code Stop hook), teeth install-hook --test-cmd CMD"
    h = argparse.ArgumentParser(prog="teeth hook")
    h.add_argument("--test-cmd", required=True)
    h.add_argument("--min-score", type=float, default=0.8)
    h.add_argument("--max-mutants", type=int, default=20)
    h.add_argument("--base")
    h.add_argument("-j", "--jobs", type=int, default=2)
    h.add_argument("--timeout", type=float, default=120)
    ih = argparse.ArgumentParser(prog="teeth install-hook")
    ih.add_argument("--test-cmd", required=True)
    ih.add_argument("--min-score", type=float, default=0.8)
    ih.add_argument("--max-mutants", type=int, default=20)
    ih.add_argument("--settings")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["hook"]:
        return hook(h.parse_args(argv[1:]), stdin or sys.stdin)
    if argv[:1] == ["install-hook"]:
        return install_hook(ih.parse_args(argv[1:]), out)
    a = ap.parse_args(argv)
    if not a.test_cmd and not a.list:
        ap.error("give the test command, e.g.  teeth 'pytest -q'")
    repo = os.path.abspath(a.repo)
    mutants, waived = collect(repo, a.base or default_base(repo))
    mutants = sample(mutants, a.max_mutants, a.seed)
    if a.list:
        for m in mutants:
            out.write("%s:%d  %-10s %s  →  %s\n" % (m["file"], m["line"], m["op"], m["before"], m["after"]))
        out.write("%d mutant(s)\n" % len(mutants))
        return 0
    results = evaluate(repo, mutants, a.test_cmd, a.jobs, a.timeout)
    if a.json:
        out.write(json.dumps({"score": score(results), "results": [{k: v for k, v in r.items() if k != "new_line"}
                                                                      for r in results], "waived": waived}, indent=2) + "\n")
    else:
        report(results, waived, out, a.format)
    s = score(results)
    return 1 if a.min_score is not None and s is not None and s < a.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
