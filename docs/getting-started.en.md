<!-- 語言 / Language: [繁體中文](getting-started.md) · **English** -->

# Applying this template from scratch

This document is for someone with **nothing set up yet**. The goal: start from a
clean machine and get all the way to "the template is really installed,
protection is really in effect, first task done."

Every step comes with **how to confirm this step worked** — this template's
biggest risk is "it looks installed but protection isn't actually in effect," so
verification matters more than installation.

> This is an English translation of [`getting-started.md`](getting-started.md);
> the Chinese version is the source of truth.

> **In a hurry?** After installing Python + git + Claude Code (see §0),
> `python3 scripts/setup.py` does §2–§4 (generate config, pick protection level,
> create version number) in one go and prints the next commands. This document is
> for those who want to understand **what each step does and how to confirm it** —
> the wizard runs it for you, this helps you understand it.

---

## 0. Prerequisites

| Need | Why | How to confirm |
|---|---|---|
| **Python 3.9+** | All scripts are Python, no third-party packages needed | `python3 --version` (Windows: `py --version`) |
| **git** | Getting the template, and the "commit after each task" workflow | `git --version` |
| **Claude Code** | The dispatch flow needs sub-agent and hook support | typing `claude` in the terminal enters it |

No Python: install from [python.org](https://www.python.org/downloads/); on
Windows check "Add python.exe to PATH" during install.

**No** packages required. `psutil` is optional — installing it makes the service
scan more accurate (`pip install psutil`); without it things still run, the port
list is just marked incomplete.

> Can it be used without Claude Code? The scripts themselves (scan, seal, audit,
> estimate) are standalone Python programs and run anywhere. But
> "implementer/verifier separation" needs the ability to spawn sub-agents with no
> shared context — that part relies on Claude Code or a tool of equivalent
> capability.

---

## 1. Get the template

### Path A: brand-new project

```bash
git clone https://github.com/oilotaku/harness-template.git my-project
cd my-project
```

### Path B: apply to an existing project

Copy these into your project root:

```
CLAUDE.md          .claude/          scripts/          docs/          templates/
```

Then two things that are **easy to miss**:

1. **Add `.harness/` to `.gitignore`.** That directory holds the environment
   fingerprint, locked list, hidden-test manifest, progress checkpoints, scan
   cache — all local state that shouldn't be version-controlled.

2. **"Merge" `.claude/settings.json`, don't overwrite.** You may already have
   your own permissions or hooks. The template needs these two hooks:

   - `SessionStart` → `scripts/guard-selfcheck.py`
   - `PreToolUse` → `scripts/guard-hidden-tests.py`

   **Without the PreToolUse one, the whole pre-interception layer doesn't exist**,
   with no error message — the next step is designed to catch exactly this.

3. **Also add this `deny` set** (round three, P0-8). The enforcement body — the
   verification runner, signing and encryption, after-the-fact audit, the guard
   itself, hook config, the verifier's rules of conduct — if the implementer can
   edit it, the verification result can be forged: measurably, after rewriting
   `scripts/run-hidden-tests.py`, running verification with **a completely correct
   token** gives exit 0 and "all hidden tests passed," while all three
   after-the-fact audits report normal.

   ```json
   "deny": [
     "Write(./scripts/**)", "Edit(./scripts/**)",
     "Write(./.claude/**)", "Edit(./.claude/**)",
     "Write(./CLAUDE.md)",  "Edit(./CLAUDE.md)",
     "Write(./harness.config.json)", "Edit(./harness.config.json)"
   ]
   ```

   The guard also blocks the same set from round three on, but **only after this
   repo has sealed a task** (otherwise people maintaining the harness itself get
   blocked by their own hook). This `deny` set is a second layer; both are just
   defense-in-depth: a sub-agent with Bash writing a script to edit them still
   gets through. The real fix is to not run the in-repo code — round three sealed
   the verification code (P0-8 (a), recorded in git history, see
   `docs/history/README.md`, _Chinese_), and round four went further and moved
   verification to CI (see `docs/ci-verification.en.md`).

   > The template's own repo **deliberately lacks** this `deny` set — it is the
   > template itself, and `scripts/` is its product code. Your project isn't that
   > case, so add it.

---

## 2. Initialize

```bash
python3 scripts/init.py
```

It runs three things in order; results are only printed, it decides nothing for
you:

| Section | What to look at |
|---|---|
| Machine-capability scan | `max parallel sub-agents this machine can sustain` — that's a **capacity ceiling, not a recommendation**. Default to sequential |
| Existing-service / port scan | Suggested usable port range. If it says "can't provide," the scan is incomplete (usually psutil not installed) — **don't just pick a port and go** |
| Environment fingerprint | The first run prints "current environment set as baseline," which is normal |

Exit code: `0` normal, `1` means the fingerprint mismatched (machine changed).

Machine-readable version for the Orchestrator:

```bash
python3 scripts/init.py --json
```

---

## 3. Confirm protection is really in effect (the most important step)

```bash
python3 scripts/guard-selfcheck.py
```

**Success looks like this**:

```
===== 防作弊機制自我檢查（SessionStart）=====
防護正常：6/6 項檢查符合預期（隱藏測試讀寫皆被擋、一般檔案不受影響）。
受保護路徑（來源：預設值（沒有 harness.config.json））：
  ...
===== 結束 =====
```

(The script's output is Chinese; "防護正常：6/6" means "protection OK: 6/6".) It
actually runs a "known-should-be-blocked" payload through the guard once, so 6/6
means interception really worked **this time**, not "the config looks right."

If you see these, fix first before continuing:

| Message | Meaning | How to fix |
|---|---|---|
| can't find `scripts/guard-hidden-tests.py` | script wasn't copied in | add `scripts/` |
| some checks not as expected | the guard was broken | run `python3 scripts/test-guards.py` to see which |
| "these directories look like hidden tests but aren't in a protected path" | a second hidden-test dir (common in monorepos) | see the next step's config, or put a README in that dir stating it's unprotected |

One case it can't catch: **the hook was never mounted by Claude Code**
(`settings.json` merged wrong). To check, open a new session in Claude Code —
normally the self-check above appears automatically at session start. **No output
at all = the hook isn't in effect.**

---

## 4. Non-Python projects: you must set `harness.config.json`

The template assumes your tests are in `tests/public` and `tests/hidden`. Go,
Rust, TypeScript conventions don't look like that, and **without configuration
protection can't find your tests, which means no protection at all**.

Create in the project root:

```json
{
  "public_test_paths": ["src/**/__tests__"],
  "hidden_test_paths": [".harness-hidden-staging"],
  "hidden_test_command": "npx vitest run --dir {dir}"
}
```

When config is wrong the scripts **error out directly** rather than silently
falling back to defaults — being able to turn off protection with one typo and no
signal is the least acceptable failure mode in this template. Full fields in
`docs/multi-language-support.md` _(Chinese)_.

> Easiest of all: `python3 scripts/setup.py` detects your language and fills this
> in for you.

---

### Want it lighter: minimal mode

If the hook setup in step 3 feels too heavy, you can turn on only layer 1
(physical isolation):

```json
{ "protection": { "level": "minimal" } }
```

Then you don't need the `.claude/settings.json` hooks; three scripts (seal, run,
discard) run it, and "the implementer can't see the hidden tests" still holds.
The cost is **public tests being changed won't be caught**, plus one operational
discipline: seal immediately after writing tests, don't leave them in staging
overnight. Full trade-off table in
[`protection-levels.en.md`](protection-levels.en.md).

The layers you turn off get said out loud every time (at seal, at verify, every
session), so "thinking all four are on when only one is" can't happen.

### Want verification somewhere the implementer can't touch: CI verification

This template can't block a sub-agent "determined to bypass and sharing the same
OS user as the verifier" — that's not an item still to be fixed, it's the
structural endpoint of local self-checking. To get past it, verification has to
move to GitHub Actions:

```json
{ "protection": { "ci_verification": true } }
```

The setup steps (commit the sealed bundle to the **default branch**, store the
token as a repository secret, and why branch protection is required not
suggested) are in [`ci-verification.en.md`](ci-verification.en.md).

---

## 5. Walk the full flow once and watch the mechanism move

### 5.1 Read the example (won't touch your project)

```bash
cd templates/examples/demo-fizzbuzz
python3 -m unittest discover -s tests/public -p 'test_*.py'
python3 -m unittest discover -s tests/hidden -p 'test_*.py'
```

Both pass. This example's hidden tests are **plaintext**, because their purpose is
to show you "what a good hidden test looks like" — the real flow isn't like this,
read on.

### 5.2 Actually seal once

Back in the project root, write a test file in the hidden-test staging area
(default `hidden/` under `tests/`), then:

```bash
python3 scripts/seal-hidden-tests.py --task-id T-001
```

It first locks the public tests, then encrypts and moves each file away, then
signs the manifest with the token, and finally prints a **one-time** execution
token. Then confirm four things yourself:

1. There's no plaintext left in the staging area (files moved away).
2. The vault is outside the repo and its contents are ciphertext.
3. Baseline run — proving this hidden test is red without the implementation:

   ```bash
   python3 scripts/run-hidden-tests.py --task-id T-001 --token <token> --baseline
   ```

   If red, it signs the result into the manifest; if green, it tells you
   outright "no discriminating power." This run doesn't count toward stop-loss.
4. Only the token can run the real verification:

   ```bash
   python3 scripts/run-hidden-tests.py --task-id T-001 --token <token>
   ```

   Deliberately mistype the token and it refuses; use a script to change
   `test_command` in `.harness/hidden-manifest.json` and it also refuses
   (signature mismatch) — **the lack of a rescue path is deliberate**; a lost
   token means rewriting the hidden tests and sealing again. A back door is a
   bypass.

   The most common cause of "loss" in practice isn't a typo, it's **hitting a
   usage cap while running tests and the token-holding session being reclaimed**.
   Then don't retry against the undecryptable ciphertext; go through the
   discard-and-redo flow (see §7 and `docs/implementer-verifier-workflow.md`,
   _Chinese_):

   ```bash
   python3 scripts/discard-sealed-task.py --task-id T-001 --token-lost --confirm
   ```

   It deletes the now-useless ciphertext, leaves a tombstone in the manifest, and
   tells you how to rewrite. Discarding **does not** launder history: on re-seal
   the discard count goes into the signed new entry.

> **It's blocked in Claude Code, not in a plain terminal.**
> `guard-hidden-tests.py` is Claude Code's PreToolUse hook, in effect only when
> Claude Code calls a tool. So the operations above with "a staging path in the
> command string" run fine in your own terminal, but are refused when Claude Code
> runs them for you — **which is exactly what it should do** (the implementer
> can't touch the staging area).

---

## 6. Your first real task

1. Open the project in Claude Code and use `/task-plan` to throw in your
   requirement (one sentence is fine). The Orchestrator does: clarify → scan →
   decompose → assign models → **estimate time** → **decide whether to install
   skills**, producing a plan for your approval.
2. After you approve, use `/task-dispatch` to dispatch. The order is fixed:
   **verifier writes tests and seals first → implementer then starts → verifier
   verifies**.
3. When verification fails, look at the objective count before deciding whether to
   dispatch another round:

   ```bash
   python3 scripts/show-attempts.py --task-id T-001
   ```

   Consecutive failures usually mean **the spec is unclear**, not that the
   implementer isn't trying hard enough.

4. Commit after each task. On interruption you lose at most one task's progress
   (reasons in `docs/token-strategy.md` §3, _Chinese_).

---

## 7. Common snags

| Symptom | Cause | What to do |
|---|---|---|
| No self-check output at the start of a new session | hook not mounted (`settings.json` merged wrong) | check §1 Path B item 2 |
| `拒絕：指令裡出現了隱藏測試暫存區的路徑` (refused: the command mentions a staging path) | **expected behavior**, not broken | to write a path into file content use the Write/Edit tools; to run hidden tests use `run-hidden-tests.py` |
| Says fingerprint mismatch every time | machine really changed | after confirming, `python3 scripts/env-guard.py --update`. A container/CI random hostname **won't** false-alarm, so if it fires it really changed |
| Windows console crashes printing Chinese | old-version issue | all scripts now switch to UTF-8 at startup, no `PYTHONUTF8` needed. Still failing? Confirm Python ≥ 3.9 |
| Port suggestion is "can't provide" | incomplete scan | `pip install psutil` and rescan; until then confirm by hand, don't guess |
| Lost the token (most common: hit a usage cap while running tests, session reclaimed) | no rescue path (deliberate) | `python3 scripts/discard-sealed-task.py --task-id <id> --token-lost --confirm`, then rewrite the hidden tests against the same task-spec and re-seal |
| Session start shows "cleaned up N leftover decryption dirs" | the previous run was force-killed (cap, kill), plaintext was on disk since | already cleaned up. Write it into the verification report; the plaintext was in the vault, a path the guard covers |
| Want to use Chinese for `task_id` | under a locale like `LC_ALL=C`, non-ASCII args can't be encoded at the OS level | use `T-001`-style `task_id`, put Chinese in the task-spec title |

---

## 8. Confirm the whole thing is healthy

After changing any mechanism (or to confirm a complete install):

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py \
  && python3 scripts/test-vault.py && python3 scripts/test-env-guard.py \
  && python3 scripts/test-config.py && python3 scripts/test-scan-json.py \
  && python3 scripts/test-timing.py && python3 scripts/test-skills.py \
  && python3 scripts/test-attempts.py && python3 scripts/test-version.py \
  && python3 scripts/test-design.py && python3 scripts/test-ci-verify.py \
  && python3 scripts/test-protection-levels.py && python3 scripts/test-setup.py
```

Fourteen suites, 466 cases, should all pass. This is also the fastest "did I
install it completely" check.

---

## 9. When you want to remove it

Removing the two hooks from `.claude/settings.json` disables protection. But note
**don't keep only half**:

- Keep `PreToolUse` without `SessionStart` → you won't know when the mechanism
  breaks.
- Keep interception without sealing (not running `seal-hidden-tests.py`) → hidden
  tests sit in the working tree as plaintext, and interception is enumerative and
  can't cover everything.

The relationship of the four layers is in `README.md`'s "What the anti-cheat
mechanism is made of" (or [`README.en.md`](../README.en.md)).
