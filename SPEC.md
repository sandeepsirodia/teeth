# teeth — SPEC

> Your tests are green. Do they bite?

Diff-scoped mutation testing. teeth mutates **only the lines your branch changed** (flips a `>`, deletes a check, swaps a return value), reruns your tests, and reports every mutation your tests failed to catch. It exists because agents write tests that pass; teeth checks whether they'd *fail* when the code is wrong.

## Who it's for
- Anyone merging agent-written tests.
- Reviewers who want one number that says whether a PR's tests actually cover its behavior.
- Agent loops: as a Stop hook, it pushes back with "your new test doesn't catch this mutation".

## Must have (v1)
1. **`teeth [--base main] -- <test command>`**: finds the changed lines (`git diff <merge-base>`), generates mutants on only those lines, and runs the test command once per mutant.
2. **Mutators**, language-agnostic, as token-level text rewrites with per-language comment and string awareness for Python, JS/TS, Go and Rust:
   - comparison flip: `<` ↔ `<=`, `>` ↔ `>=`, `==` ↔ `!=`
   - boolean flip: `and` ↔ `or`, `&&` ↔ `||`, `true` ↔ `false`, `not x` → `x`
   - arithmetic swap: `+` ↔ `-`, `*` ↔ `/`
   - constant nudge: `0` ↔ `1`, `n` → `n+1` on literals
   - return blanking: `return x` → `return None` / `null` / zero value
   - statement deletion: drop a single-line call or `raise` / `throw`
   - conditional forcing: `if (cond)` → `if (true)` / `if (false)`
3. **Report:** a mutation score (killed / total) and each **survivor** as `file:line  original → mutant`, grouped by file.
4. **Speed:**
   - Run only tests relevant to the file (`--test-cmd-per-file 'pytest {test_for_file}'`, optional).
   - Run mutants in parallel, each in its own temporary copy (`-j`).
   - Cap the count with `--max-mutants 50`, sampled across files.
   - Stop each mutant on the first failing test.
5. **Never touches your working tree.** Mutants live in temporary copies, and crashes clean up after themselves.
6. **Equivalent-mutant hygiene:** skip mutants that don't change the file's bytes after normalization, and let users waive a line with `# teeth: ignore` (plus a reason).
7. **Gate mode:** `--min-score 0.8` exits 1 below the threshold. `--format github` emits PR annotations.
8. **Claude Code Stop hook:** `teeth install-hook` blocks "done" when new tests let mutants survive, and passes the survivors back to the agent.
9. **Stdlib only.**

## Won't do (v1)
- AST-perfect mutation per language (text rewrites plus lexing is the deliberate tradeoff).
- Mutating unchanged code.
- Detecting equivalent mutants automatically beyond byte normalization.

## Expectations → test cases
Fixtures are tiny git repos (Python and JS) with a base branch and a feature branch.

| ID | Given | When | Then |
|---|---|---|---|
| E1 | A branch adding `if amount > limit: raise` with **no** test for the boundary | Run | The `>` → `>=` mutant survives and is reported at the correct `file:line` |
| E2 | The same branch plus a boundary test | Run | That mutant is killed; the score goes up |
| E3 | Changed lines only | Mutant generation | No mutant touches an unchanged line (checked over all mutants) |
| E4 | A `>` inside a string literal or comment | Mutant generation | Not mutated |
| E5 | Any run, including a test command that crashes | After the run | Working tree byte-identical, no temp dirs left, `git status` clean |
| E6 | A mutant whose tests hang | `--timeout 5` | Counted as killed-by-timeout, reported separately; process group killed |
| E7 | `--min-score 0.8` with a score of 0.6 | Exit code | 1, with the survivors listed |
| E8 | A line with `# teeth: ignore — equivalent` | Run | No mutants for that line; the waiver is listed in the report |
| E9 | A 300-line diff, `--max-mutants 50` | Run | Exactly ≤50 mutants, sampled across all changed files (not the first 50 in file order) |
| E10 | `-j 4` | Run | Same kills and survivors as `-j 1` (deterministic) |
| E11 | Hook installed; the agent adds a weak test and says done | Stop hook | Blocks, and returns the survivor list as the reason |
| E12 | A fixture with known mutation-testing ground truth (hand-labelled kill/survive per mutant) | Run | 100% agreement |

## Launch number
Run teeth on 10 real agent-written PRs (from public repos using Claude Code or Codex, found via `Co-Authored-By` trailers). Report the median mutation score of agent-written tests versus human-written tests in the same repos. Commit the list of PRs and the raw output.

## Done when
E1–E12 pass. The README opens with a real survivor from a real PR.
