<h1 align="center">teeth</h1>

<p align="center">
  <em>Your tests are green. Do they bite?</em>
</p>

<p align="center">
  <a href="https://github.com/sandeepsirodia/teeth/actions/workflows/ci.yml"><img src="https://github.com/sandeepsirodia/teeth/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/dependencies-0-111111?style=flat-square" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/langs-Python%20·%20JS%2FTS%20·%20Go%20·%20Rust-111111?style=flat-square" alt="Languages">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT">
</p>

---

Your agent adds a spending limit:

```python
if amount > limit:
    raise ValueError("over limit")
```

It writes two tests: one well under the limit, one well over. All green. PR approved.

Now change `>` to `>=`. **The tests still pass.** Nobody checked what happens *at* the limit, which is exactly where the bug report will come from.

Your tests execute that line, so coverage says 100%. They just don't *check* it. A test suite can be green and blind at the same time.

**teeth finds the blind spots.** It makes small, deliberate bugs in the lines you changed, one at a time, and reruns your tests. Every bug your tests *don't* notice is a behavior nobody is checking:

```console
$ teeth 'pytest -q'
Mutation score: 3/4 caught (75%)

1 mutant(s) survived. Your tests did not notice these behavior changes:

  billing.py
    :2    comparison if amount > limit:
               → if amount >= limit:
```

One boundary test later: 4/4, and that bug can't ship.

## Try it on your branch

```bash
uvx --from git+https://github.com/sandeepsirodia/teeth teeth 'pytest -q'        # or 'npm test', 'go test ./...', 'cargo test'
```

It only mutates **lines your branch changed** (vs `main`), so it takes seconds to minutes, not the hours classic mutation testing takes on a whole codebase.

## What it breaks on purpose

| Mutation | Example |
|---|---|
| comparisons | `>` → `>=`, `==` → `!=`, `===` → `!==` |
| booleans | `and` ↔ `or`, `&&` ↔ `\|\|`, `True` ↔ `False`, drop a `not` |
| arithmetic | `+` ↔ `-`, `*` ↔ `/` |
| constants | `0` ↔ `1`, `n` → `n+1` |
| returns | `return total` → `return None` / `return null` |
| deleted checks | `raise …` / `throw …` / a bare call → removed |
| forced branches | `if cond:` → `if True:` and `if False:` |

Strings and comments are never touched: each file is lexed first (quotes, `#`/`//`/`/* */`, Python triple quotes, JS template literals, Rust lifetimes).

## Make your agent do it: Claude Code Stop hook

```bash
teeth install-hook --test-cmd 'pytest -q' --min-score 0.8
```

Now when your agent says "done, all tests pass", teeth runs first. If mutants survive, the agent gets told exactly which behavior changes its tests missed, and it goes back to write the assertion. It only blocks once per turn, so it can never loop.

## On a real Claude-written commit

<p align="center"><img src="https://raw.githubusercontent.com/sandeepsirodia/teeth/main/assets/llm-run.svg" alt="teeth on simonw/llm commit e1267a4: 9 of 15 mutants caught; survivors at the condensing length boundary, the empty-container case and a missing model id" width="820"></p>

[simonw/llm](https://github.com/simonw/llm) commit [`e1267a4`](https://github.com/simonw/llm/commit/e1267a4) (co-authored with Claude) added JSON payload condensing plus 150 lines of tests. teeth, 20 sampled mutants, 45 seconds:

```
Mutation score: 9/15 caught (60%)
  llm/logs.py
    :925  comparison if size >= _CONDENSE_MIN_LENGTH:
               → if size > _CONDENSE_MIN_LENGTH:
    :918  condition  if isinstance(value, (dict, list)) and value:
               → if True:
    :1026 condition  if not model_id:
               → if False:
    …
```

Those are real questions for the tests: nothing pins the exact length where condensing starts, the empty-container case, or a missing model id. That's no criticism of a well-tested project. It's what 150 lines of good tests still leave open, and it takes a mutation tool to see it.

I first ran it against the commit's own test file (160 tests), then against every llm test file that's green in my environment (763 tests): **the same 9/15 and the same six survivors both times**, so it isn't an artifact of a narrow test selection.

The first time I ran this, teeth said **0/16**. llm is installed in editable mode, so every import went back to the original checkout and none of the mutants ever ran. That's now fixed (the copy is put first on `PYTHONPATH`), and more importantly **guarded**: teeth replaces each changed file with garbage before starting, and if your tests still pass, it tells you they never load that file instead of reporting fake survivors.

## More real runs: Claude-written commits in simonw/datasette

| Commit | What it did | Score |
|---|---|---|
| [`96226621`](https://github.com/simonw/datasette/commit/96226621) | Fix SQL injection in `escape_sqlite()` | **3/3 caught**: the fix is well pinned |
| [`211e70d4`](https://github.com/simonw/datasette/commit/211e70d4) | Return 400 instead of 500 for wrong-arity row URLs | **3/3 caught** |
| [`591b909a`](https://github.com/simonw/datasette/commit/591b909a) | Escape table names containing `[brackets]` | 0/1 with the commit's own test file, **1/1 with the whole suite** |
| [`1c514d69`](https://github.com/simonw/datasette/commit/1c514d69) | Fix open redirect via backslash | nothing to mutate (the fix is inside a regex string) |

Two lessons the third and fourth rows taught me:

- **A survivor only means "not caught by the tests you ran".** Run just the changed test file and teeth will report survivors that the rest of the suite catches. Pass your real test command (the whole suite, or a fast marker-selected subset) before reading anything into a low score.
- **teeth refuses to score a red suite.** My first datasette run stopped with "your tests fail without any mutation" because I'd installed dependencies at the wrong commit. That's the tool working as designed: a mutation score over failing tests would be meaningless.

## I ran it on my own code first

The morning I wrote teeth, I pointed it at a fix I'd just committed to [readme-lies](https://github.com/sandeepsirodia/readme-lies):

```
Mutation score: 5/6 caught (83%)
  readme_lies.py
    :285  constant   … m.group(1) …
               → … m.group(0) …
```

I looked, and it's an **equivalent mutant**: `group(0)` is `group(1)` plus the tool's own name, which never contains a flag, so behavior can't change. No test could catch it, because there's nothing to catch. That's the honest limit of mutation testing, and why waivers exist:

```python
args = re.sub(…, m.group(1))  # teeth: ignore — group(0) only adds the tool name; equivalent
```

Waivers need a reason, and the report lists them, so they stay visible in review.

## Built to be trusted

teeth writing a wrong verdict would be worse than no verdict, so:

- **Your working tree is never touched.** Mutants live in temporary copies; heavy directories like `node_modules` are symlinked, not copied.
- **Every mutant really runs.** Build caches (`__pycache__`, etc.) are stripped and timestamps are bumped. Without that, a same-size edit like `n -= 1` → `n -= 0` can silently run the *cached original*. I hit exactly that bug while building teeth; there's now a regression test for it.
- **Every file is proven to be exercised.** Before any mutant runs, each changed file is replaced with garbage. If your tests still pass, that file isn't loaded by your test command (not imported, or imported from an installed copy), so its mutants are skipped and flagged, never reported as fake survivors.
- **Infinite loops count as caught.** A mutant that makes your tests hang is killed at `--timeout`, whole process tree included.
- **Deterministic.** Same results with `-j 1` and `-j 8`. `--max-mutants 50` samples evenly across files with a fixed seed.

## Options

| | |
|---|---|
| `--base main` | What to diff against (default: `origin/HEAD`, `main` or `master`) |
| `-j 8` | Parallel workers, each in its own copy |
| `--max-mutants 50` | Cap the run for big diffs |
| `--min-score 0.8` | Exit 1 below this, for a CI gate |
| `--format github` | Survivors as PR annotations |
| `--list` | Show the mutants without running anything |
| `--json` | Machine-readable |
| `{file}` in the test command | Replaced by the mutated file, for per-file test runs |

## Honest limits

- Mutations are text-level (with real lexing), not AST-perfect. Some mutants won't compile (e.g. a `<` inside TypeScript generics); those count as caught, which slightly flatters the score.
- **Regex patterns and string constants are never mutated**, because strings are masked so they can't be corrupted. A real example: datasette's fix for an open redirect changed `re.sub(r"^/+", …)` to `re.sub(r"^[/\\]+", …)`. The whole fix lives inside a string, so teeth reports "nothing to mutate" for it. Mutation testing of regexes needs a different tool.
- Equivalent mutants exist (see above). teeth can't prove equivalence; you waive them with a reason.
- A score is about **your changed lines only**. It says nothing about code you didn't touch.

## Prior art, and what's new here

Mutation testing is decades old, and diff-scoped runs aren't new either: [cargo-mutants `--in-diff`](https://mutants.rs/in-diff.html), [Stryker](https://stryker-mutator.io/), [PIT](https://pitest.org/), [mutmut](https://github.com/boxed/mutmut), gomutants and mull all do real mutation testing. **They understand their language far better than teeth does. If one exists for your stack, use it.**

teeth is for the gap around them:
- **one zero-install tool** across Python, JS/TS, Go and Rust
- **a Claude Code Stop hook**, so the agent that wrote the tests has to answer for them
- **a canary check** that refuses to report mutants in files your tests never load

<details>
<summary><b>Development</b></summary>

```bash
python -m unittest discover -s tests -v
```

Tests map 1:1 to [SPEC.md](SPEC.md). They build real git repos with a `main` and a `feature` branch. E12 checks teeth against a hand-labelled answer key: which mutants the tests should catch and which they shouldn't.

</details>

<p align="center"><sub>MIT © Sandeep Sirodia · If teeth found a blind spot before production did, a ⭐ helps others find it.</sub></p>
