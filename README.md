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
- Equivalent mutants exist (see above). teeth can't prove equivalence; you waive them with a reason.
- A score is about **your changed lines only**. It says nothing about code you didn't touch.

<details>
<summary><b>Development</b></summary>

```bash
python -m unittest discover -s tests -v
```

Tests map 1:1 to [SPEC.md](SPEC.md). They build real git repos with a `main` and a `feature` branch. E12 checks teeth against a hand-labelled answer key: which mutants the tests should catch and which they shouldn't.

</details>

<p align="center"><sub>MIT © Sandeep Sirodia · If teeth found a blind spot before production did, a ⭐ helps others find it.</sub></p>
