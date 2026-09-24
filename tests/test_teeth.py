"""Tests map 1:1 to SPEC.md (E1..E12). Fixture repos are real git repos with a main branch and a
feature branch, built from scratch per test."""
import glob
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import teeth  # noqa: E402

PY = sys.executable
TEST_CMD = "%s -m unittest discover -s tests -q" % PY

BASE_CODE = "def charge(amount, limit):\n    return amount\n"
FEATURE_CODE = textwrap.dedent("""\
    def charge(amount, limit):
        if amount > limit:  # guard
            raise ValueError("over limit")
        return amount
""")
WEAK_TEST = textwrap.dedent("""\
    import unittest
    from billing import charge

    class T(unittest.TestCase):
        def test_under(self):
            self.assertEqual(charge(50, 100), 50)

        def test_over(self):
            with self.assertRaises(ValueError):
                charge(150, 100)
""")
BOUNDARY_TEST = WEAK_TEST + textwrap.dedent("""\

        def test_at_limit(self):
            self.assertEqual(charge(100, 100), 100)
""").replace("\n", "\n    ").rstrip() + "\n"


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null", *args], cwd=repo, check=True, capture_output=True)


def write(repo, path, text):
    p = os.path.join(repo, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def fixture(test_text=WEAK_TEST, code=FEATURE_CODE):
    repo = tempfile.mkdtemp(prefix="teeth-fixture-")
    write(repo, "billing.py", BASE_CODE)
    write(repo, "tests/test_billing.py", WEAK_TEST.split("        def test_over")[0].rstrip() + "\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    write(repo, "billing.py", code)
    write(repo, "tests/test_billing.py", test_text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "add limit guard")
    return repo


def cli(repo, *argv, stdin=None):
    out = io.StringIO()
    code = teeth.main(["--repo", repo, *argv] if argv[:1] not in (["hook"], ["install-hook"]) else list(argv),
                      out=out, stdin=stdin)
    return code, out.getvalue()


def run_json(repo, *extra):
    code, out = cli(repo, "--json", *extra, TEST_CMD)
    return json.loads(out)


class TestSpec(unittest.TestCase):
    def test_e1_boundary_mutant_survives(self):
        data = run_json(fixture())
        survivors = [r for r in data["results"] if r["status"] == "survived"]
        self.assertEqual([(r["file"], r["line"], r["after"]) for r in survivors],
                         [("billing.py", 2, "if amount >= limit:  # guard")])

    def test_e2_boundary_test_kills_it(self):
        weak, strong = run_json(fixture())["score"], run_json(fixture(BOUNDARY_TEST))["score"]
        self.assertEqual(strong, 1.0)
        self.assertLess(weak, strong)

    def test_e3_only_changed_lines(self):
        repo = fixture()
        changed = teeth.changed_lines(repo, "main")
        mutants, _ = teeth.collect(repo, "main")
        self.assertTrue(mutants)
        for m in mutants:
            self.assertIn(m["line"], changed[m["file"]])
        self.assertNotIn(4, {m["line"] for m in mutants})  # `return amount` existed on main

    def test_e4_strings_and_comments_untouched(self):
        py = 'x = "a > b and 1"  # c > d or 0\ny = f"{a > b}" if z else 2\ns = """\n> 3\n"""\n'
        mutants, _ = teeth.mutants_for_file("m.py", py, {1, 2, 3, 4, 5}, "py")
        for m in mutants:
            orig = py.split("\n")[m["line"] - 1]
            # the only mutable code: line 2's `if z else 2` constant and conditional parts outside the f-string
            self.assertNotIn(m["line"], (1, 3, 4, 5), m)
            self.assertEqual(m["after"].split('"')[1] if '"' in orig else "", orig.split('"')[1] if '"' in orig else "")
        js = 'const s = `x > ${y}`; /* a > b */ if (a > b) { go(); } // c > d\n'
        mutants, _ = teeth.mutants_for_file("m.js", js, {1}, "js")
        self.assertTrue(any("if (a >= b)" in m["after"] for m in mutants))
        for m in mutants:
            self.assertIn("`x > ${y}`", m["after"])
            self.assertIn("/* a > b */", m["after"])

    def test_e5_working_tree_untouched(self):
        repo = fixture()
        before = {p: open(p, "rb").read() for p in glob.glob(repo + "/**/*.py", recursive=True)}
        leftovers_before = set(glob.glob(os.path.join(tempfile.gettempdir(), "teeth-*")))
        cli(repo, "--json", TEST_CMD)
        cli(repo, "--json", "sh -c 'exit 0'")  # a test command that "passes" everything
        after = {p: open(p, "rb").read() for p in glob.glob(repo + "/**/*.py", recursive=True)}
        self.assertEqual(before, after)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")
        leftovers = set(glob.glob(os.path.join(tempfile.gettempdir(), "teeth-*"))) - leftovers_before
        self.assertEqual({p for p in leftovers if "fixture" not in p}, set())

    def test_e6_timeout_counts_as_caught(self):
        code = "def countdown(n):\n    while n > 0:\n        n -= 1\n    return n\n"
        test = "import unittest\nfrom billing import countdown\n\nclass T(unittest.TestCase):\n" \
               "    def test_it(self):\n        self.assertEqual(countdown(3), 0)\n"
        repo = fixture(test, code)
        data = run_json(repo, "--timeout", "3")
        statuses = {r["after"]: r["status"] for r in data["results"]}
        self.assertEqual(statuses["n -= 0"], "timeout")  # 1 -> 0 makes the loop infinite

    def test_e7_min_score_gate(self):
        code, out = cli(fixture(), "--min-score", "0.9", TEST_CMD)
        self.assertEqual(code, 1)
        self.assertIn("survived", out)
        self.assertEqual(cli(fixture(BOUNDARY_TEST), "--min-score", "0.9", TEST_CMD)[0], 0)

    def test_e8_waiver(self):
        code = FEATURE_CODE.replace("# guard", "# teeth: ignore — equivalent at the boundary by design")
        data = run_json(fixture(WEAK_TEST, code))
        self.assertNotIn(2, {r["line"] for r in data["results"]})
        self.assertEqual(data["waived"][0]["line"], 2)
        self.assertIn("equivalent", data["waived"][0]["reason"])

    def test_e9_sampling_spreads_across_files(self):
        repo = fixture()
        for i in range(3):
            write(repo, "mod%d.py" % i, "".join("x%d = %d > %d\n" % (j, j, j + 1) for j in range(40)))
        mutants, _ = teeth.collect(repo, "main")
        self.assertGreater(len(mutants), 50)
        picked = teeth.sample(mutants, 50)
        self.assertEqual(len(picked), 50)
        self.assertEqual({m["file"] for m in picked} >= {"mod0.py", "mod1.py", "mod2.py"}, True)
        self.assertEqual(picked, teeth.sample(mutants, 50))  # deterministic

    def test_e10_parallel_is_deterministic(self):
        repo = fixture()
        a = [(r["line"], r["after"], r["status"]) for r in run_json(repo, "-j", "1")["results"]]
        b = [(r["line"], r["after"], r["status"]) for r in run_json(repo, "-j", "4")["results"]]
        self.assertEqual(a, b)

    def test_e11_stop_hook_blocks_weak_tests(self):
        repo = fixture()
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            teeth.main(["hook", "--test-cmd", TEST_CMD, "--base", "main"],
                       stdin=io.StringIO(json.dumps({"cwd": repo, "stop_hook_active": False})))
            decision = json.loads(out.getvalue())
            self.assertEqual(decision["decision"], "block")
            self.assertIn("billing.py:2", decision["reason"])
            out.truncate(0), out.seek(0)
            teeth.main(["hook", "--test-cmd", TEST_CMD, "--base", "main"],
                       stdin=io.StringIO(json.dumps({"cwd": repo, "stop_hook_active": True})))
            self.assertEqual(out.getvalue(), "")  # never loops
            out.truncate(0), out.seek(0)
            strong = fixture(BOUNDARY_TEST)
            teeth.main(["hook", "--test-cmd", TEST_CMD, "--base", "main"],
                       stdin=io.StringIO(json.dumps({"cwd": strong})))
            self.assertEqual(out.getvalue(), "")  # good tests: allowed to stop
        finally:
            sys.stdout = old

    def test_e12_ground_truth(self):
        # Hand-labelled: the only behavior change the weak tests miss is the boundary (amount == limit).
        expected = {
            "if amount >= limit:  # guard": "survived",   # charge(100, 100) is never tested
            "if True:": "killed",                          # charge(50, 100) would raise
            "if False:": "killed",                         # charge(150, 100) would not raise
            "pass": "killed",                              # over-limit no longer raises
        }
        got = {r["after"]: r["status"] for r in run_json(fixture())["results"]}
        self.assertEqual(got, expected)


class TestMutantsReallyRun(unittest.TestCase):
    def test_same_size_mutant_is_not_masked_by_bytecode_cache(self):
        # Regression: `n -= 1` -> `n -= 0` keeps file size; a stale __pycache__ made it silently "survive".
        code = "def countdown(n):\n    while n > 0:\n        n -= 1\n    return n\n"
        test = "import unittest\nfrom billing import countdown\n\nclass T(unittest.TestCase):\n" \
               "    def test_it(self):\n        self.assertEqual(countdown(3), 0)\n"
        repo = fixture(test, code)
        subprocess.run(TEST_CMD, shell=True, cwd=repo, capture_output=True)  # leave a real __pycache__ behind
        self.assertTrue(glob.glob(os.path.join(repo, "__pycache__", "*.pyc")))
        for _ in range(3):
            data = run_json(repo, "--timeout", "3")
            self.assertEqual({r["after"]: r["status"] for r in data["results"]}["n -= 0"], "timeout")


class TestRealWorldTraps(unittest.TestCase):
    """Regressions from running teeth on a real repo (simonw/llm): 0/16 'caught' was a lie."""

    def test_editable_install_elsewhere_is_detected_not_reported_as_survivors(self):
        # Tests import billing from a *different* directory (like an editable install pointing at the
        # original checkout), so teeth's mutated copy is never loaded.
        repo = fixture()
        elsewhere = tempfile.mkdtemp(prefix="teeth-fixture-installed-")
        with open(os.path.join(elsewhere, "billing.py"), "w") as f:
            f.write(FEATURE_CODE)
        cmd = "PYTHONPATH=%s %s -I -c \"import sys; sys.path.insert(0, %r); import unittest; " \
              "unittest.main(module=None, argv=['x', 'discover', '-s', 'tests', '-q'])\"" % (elsewhere, PY, elsewhere)
        data = json.loads(cli(repo, "--json", cmd)[1])
        self.assertEqual({r["status"] for r in data["results"]}, {"unexercised"})
        self.assertIsNone(data["score"])
        code, out = cli(repo, cmd)
        self.assertIn("never load them", out)

    def test_src_layout_copy_is_imported(self):
        repo = tempfile.mkdtemp(prefix="teeth-fixture-src-")
        write(repo, "src/billing.py", BASE_CODE)
        write(repo, "tests/test_billing.py", WEAK_TEST.split("        def test_over")[0].rstrip() + "\n")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "base")
        git(repo, "checkout", "-qb", "feature")
        write(repo, "src/billing.py", FEATURE_CODE)
        write(repo, "tests/test_billing.py", WEAK_TEST)
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "guard")
        # the original checkout is on sys.path too (like an editable install): the copy must still win
        cmd = "PYTHONPATH=$PYTHONPATH:%s/src %s -m unittest discover -s tests -q" % (repo, PY)
        data = json.loads(cli(repo, "--json", cmd)[1])
        self.assertEqual([r["after"] for r in data["results"] if r["status"] == "survived"],
                         ["if amount >= limit:  # guard"])

    def test_multiline_return_is_not_blanked(self):
        py = "def f(x):\n    return json.dumps(\n        x,\n    )\n"
        mutants, _ = teeth.mutants_for_file("m.py", py, {2}, "py")
        self.assertFalse([m for m in mutants if m["op"] == "return"])


class TestReviewFindings(unittest.TestCase):
    def test_target_dir_is_never_shared_with_the_real_checkout(self):
        repo = fixture()
        os.makedirs(os.path.join(repo, "target", "debug"))
        write(repo, "Cargo.toml", "[package]\nname = 'x'\n")
        copy = teeth.make_copy(repo)
        try:
            self.assertFalse(os.path.lexists(os.path.join(copy, "target")))       # not copied, not symlinked
            seen = {}
            import unittest.mock as mock
            with mock.patch.object(teeth.subprocess, "Popen") as popen:
                popen.return_value.wait.return_value = 0
                teeth.run_tests("cargo test", copy, 5)
                seen = popen.call_args.kwargs["env"]
            self.assertEqual(seen["CARGO_TARGET_DIR"], os.path.join(copy, ".teeth-target"))
        finally:
            teeth.shutil.rmtree(copy, ignore_errors=True)

    def test_node_modules_is_still_symlinked_not_copied(self):
        repo = fixture()
        os.makedirs(os.path.join(repo, "node_modules", "pkg"))
        copy = teeth.make_copy(repo)
        try:
            self.assertTrue(os.path.islink(os.path.join(copy, "node_modules")))
        finally:
            teeth.shutil.rmtree(copy, ignore_errors=True)

    def test_repo_with_no_base_gives_a_friendly_error_not_a_traceback(self):
        repo = tempfile.mkdtemp(prefix="teeth-fixture-")
        write(repo, "a.py", "x = 1\n")
        git(repo, "init", "-q", "-b", "trunk")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "only commit")        # no main/master, and HEAD~1 doesn't exist
        with self.assertRaises(SystemExit) as cm:
            cli(repo, "--list")
        self.assertIn("--base", str(cm.exception))


class TestLexerAndMutators(unittest.TestCase):
    def test_rust_lifetimes_are_not_strings(self):
        rs = "fn f<'a>(x: &'a str) -> bool { x.len() > 3 }\n"
        mutants, _ = teeth.mutants_for_file("m.rs", rs, {1}, "rs")
        self.assertTrue(any("x.len() >= 3" in m["after"] for m in mutants))
        self.assertTrue(all("-> bool" in m["after"] for m in mutants if m["op"] == "comparison"))

    def test_arrows_are_not_comparisons(self):
        js = "const f = (a) => a;\n"
        self.assertEqual(teeth.mutants_for_file("m.js", js, {1}, "js")[0], [])

    def test_go_if_and_raw_strings(self):
        go = "if count > limit {\n    s := `a > b`\n}\n"
        mutants, _ = teeth.mutants_for_file("m.go", go, {1, 2}, "go")
        afters = {m["after"] for m in mutants}
        self.assertIn("if count >= limit {", afters)
        self.assertIn("if true {", afters)
        self.assertFalse(any(m["line"] == 2 for m in mutants))

    def test_untracked_new_files_count_as_changed(self):
        repo = fixture()
        write(repo, "new_module.py", "def f(x):\n    return x > 1\n")
        self.assertIn("new_module.py", teeth.changed_lines(repo, "main"))

    def test_test_files_are_never_mutated(self):
        self.assertNotIn("tests/test_billing.py", teeth.changed_lines(fixture(), "main"))


if __name__ == "__main__":
    unittest.main()
