<!-- 語言 / Language: [繁體中文](README.md) · **English** -->

# Harness Engineering Template

[![CI](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml/badge.svg)](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml)

**Version**: 0.1.0

> This is an English translation of [`README.md`](README.md) (Traditional Chinese).
> The Chinese version is the source of truth; if the two ever disagree, trust the
> Chinese one and please file an issue. Docs not yet translated are linked below
> and marked _(Chinese)_.

A **baseline development framework** for Claude Code (or any AI workflow that
supports sub-agents), used to turn "one vague requirement" into "several
sub-tasks that can be verified independently and keep each other honest."

## What problem this template solves

**The AI self-verification problem**

- Stop the AI from being both player and referee (implementer/verifier mixed
  together → it easily "passes its own work").
- Stop the AI from cheating to make tests pass (hard-coded return values,
  skipped tests, mocking out the core logic).
- Stop the AI from filling in the blanks and sprawling when the requirement is
  unclear.

**The engineering-discipline problem**

- Stop it from spawning parallel tasks or colliding with ports without knowing
  the machine's real capacity or what services already run.
- Stop it from being blind to where cost goes (especially on subscription plans,
  where **what blocks you is the burn rate, not the dollar amount**).
- Stop "a rule written down with nothing to make it hold" — this is the
  template's most central line.

## First, plainly: what it blocks and what it does not

This section is at the front on purpose. The most likely harm this template
does is not "too little protection" — it's **making people believe protection
is stronger than it is**. What the first three rounds fixed was exactly the
"claim contradicts the measurement" problem.

**What it blocks**: the "glance out of habit," "reflexively grep the whole
repo," "brushed against it with a tool's normal use" kind of **unintentional**
leak. After sealing, hidden tests are not in the working tree, their contents
are ciphertext, and decryption needs a token handed only to the verifier. This
layer is hard, and it relies on nobody's self-discipline.

**What grade of "encryption" this is** (so you don't misuse it): sealing uses a
SHA-256 keystream XOR (a separate key per file) implemented with the Python
standard library only — **not AES**. Its threat model is exactly the above —
stopping unintentional leaks — so it is **misread-prevention grade, not
confidentiality grade**. Do **not** put genuinely sensitive things (credentials,
personal data, keys, trade secrets) in hidden tests: that kind of data belongs in
OS permissions or dedicated secret management, not this vault. For the design
trade-offs (why not AES, how the two-time-pad is avoided) see
[`docs/ci-verification.md`](docs/ci-verification.md) §1 and the module docstring
of `scripts/hidden_vault.py`.

**What it does not block** (the default local setup): a sub-agent that is
**determined to bypass and shares the same OS user as the verifier**. The token
passes through Claude Code's transcript; the decrypted plaintext exists on disk
during test execution; and any "self-check inside the verification code" can be
bypassed by "swapping out the part that checks itself" — that is the structural
endpoint of self-checking, not an item still to be fixed.

**To block that class, you cannot add one more layer.** You have to make
verification not run in the implementer's execution environment at all: move
hidden-test execution to GitHub Actions, where the workflow and the sealed
bundle both come from the default branch, the token is a repository secret, and
that side only gets an exit code plus an encrypted report. How to set it up —
and **this approach's own new roots of trust and residual limits** (who can
change the default branch, who can read the CI log, and that the code under test
can reach the plaintext by nature) — are in
[`docs/ci-verification.en.md`](docs/ci-verification.en.md), with no whitewashing.

**Also does not block** (a second honest limit): when the implementer and the
verifier are the **same underlying model**, their errors are correlated.
Separating sessions removes "cross-contamination," not "shared priors" — given an
ambiguous spec, one model is likely to misread it the same way on both sides, so
the tests and the implementation are **wrong together** and everything goes
green. Hidden tests stop "knowing the spec yet cutting corners"; they cannot stop
"both sides genuinely misunderstanding the spec." What reduces it: writing the
task-spec so it has only one reading, assigning the implementer and verifier
different models where possible, and adding a human spec review for ambiguous or
high-risk tasks — see the "correlated misunderstanding" section of
[`docs/implementer-verifier-workflow.md`](docs/implementer-verifier-workflow.md)
(Chinese).

**Strength is optional**: four layers are all on by default (`full`), but if you
only want "the implementer cannot see the hidden tests," `minimal` runs with
three scripts and three lines of config, and each layer you turned off gets said
out loud every time. See [`docs/protection-levels.en.md`](docs/protection-levels.en.md).

## Core design: replace "rules that rely on self-discipline" with mechanisms

A rule written in a doc only counts when **something makes it hold**. Every rule
in this template maps to a mechanism, not a reminder:

| Rule | Not relying on | Relying on |
|---|---|---|
| Implementer can't see hidden tests | prompt reminders | encrypted seal outside the repo (per-file key); decryption needs a one-time token |
| Verification result can't be forged | "`.harness/` has a hook" | every manifest field the runner trusts is signed with the token; **the verification code itself is also sealed outside the repo**, and each file's sha256 is in the signature scope too — editing the in-repo copy doesn't affect verification; editing the sealed copy makes it refuse to run |
| Public tests can't be tampered with | whether pre-interception succeeded | after-the-fact sha256 audit; the list's own sha256 is in the signed manifest, so even deleting it whole is detectable |
| Hidden tests actually discriminate | "the verifier will write them well" | a mandatory baseline run after sealing: without the implementation it must be red, and the result is signed into the manifest |
| Stop-loss after ≥3 consecutive failures | implementer self-reporting | an objective count written by the side running the tests, signed per entry; the count is also signed into the manifest, so deleting records is detectable |
| Bug fixes address the root cause, not the symptom | "remember to look for siblings" | the reported minimal repro goes public, sibling variants go hidden: public green + hidden red = only that one case was fixed |
| Version number doesn't drift | remembering to update everywhere | README/code/manifest declare the version as `mirrors`, updated together and checked together |
| Screens don't get a new look per task | "follow the design mockup" | colors/spacing/type are declared by the project as role-named tokens; the implementer consumes them instead of inventing |
| Accessibility is actually verified | "a11y is an implicit acceptance criterion" | contrast is computed per pair against WCAG AA for both light and dark — the first default palette's dark border was 2.99 against a 3.00 threshold, invisible to the eye |
| Design isn't applied silently | use whatever the template ships | the default is marked "not confirmed by this project," reminded every session until someone actually asked the user |
| Verifier doesn't edit the implementation | prompt reminders | it has no `Write` tool; the skill policy also blocks code-generating skills |
| Docs, config, CI stay consistent | remembering to sync | drift tests — miss one place and it goes red |
| Verification isn't in the hands of the verified | "the verification code has a self-check" | the real verification runs on GitHub Actions: the workflow and sealed bundle both come from the **default branch**, the token is a repository secret, and the implementer side only gets an exit code plus an encrypted report |
| Protection strength is a choice, not a default | "four layers all on, or don't use it" | `protection.level` is `minimal`/`full`; the layers you turn off are announced at seal, at verify, and every session |
| Stop and confirm when the environment changes | the user paying attention | environment-fingerprint comparison, and deliberately **not treating a container's random hostname as an environment change** (a guard that always false-alarms is no guard). The first run states plainly "nothing was compared this time," and when the baseline is gitignored it separately warns that it's effectively disabled in a re-cloned environment |

### Where the paragraph above comes from

This is a record of what was actually measured, not a design statement. Locally
this mechanism blocks the "glance out of habit," "reflexively grep the whole
repo," "brushed against it with a tool's normal use" kind of **unintentional**
leak. It **does not block** a sub-agent that is "determined to bypass and shares
the same OS user as the verifier" — the token passes through Claude Code's
transcript, and the decrypted plaintext briefly exists on disk during test
execution. That needs a separate user or container, beyond this template's
scope.

⚠️ **This used to also say "deliberate bypass leaves traces (signature mismatch,
audit mismatch, attempt-count mismatch)." The third round's measurement disproved
that sentence, so it was removed.** Rewriting `scripts/run-hidden-tests.py` left
no trace at all: verification exited 0, printed "all hidden tests passed," the
token was fed into the argv of the modified code, and both `verify-locks.py` and
`guard-selfcheck.py --strict` reported normal.

That path is now closed, but **what closed it is the dispatch method, not any one
program**: the verification code is sealed outside the repo alongside the hidden
tests (`<vault>/_runner/`), each file's sha256 is in the signature scope, and
what the Orchestrator hands `verifier-reviewer` is **that absolute path**. So:

- Edit the in-repo `scripts/run-hidden-tests.py` → doesn't affect verification
  (that's not what verification runs).
- Edit any dependency of the sealed copy → sha256 mismatch, exit 2 immediately.
- The pre-interception layer also guards it: after this repo has sealed a task,
  the guard blocks writes to the enforcement body (`scripts/`, `.claude/`,
  `CLAUDE.md`, `harness.config.json`); when applying the harness to your own
  project you're also advised to add a `deny` set (see
  [`docs/getting-started.en.md`](docs/getting-started.en.md)).

**What it still does not block** (honestly recorded): if what gets swapped is the
sealed `run-hidden-tests.py` **itself**, and the swapped version removes the
self-comparison entirely, then nobody runs that check — every self-check has this
loop. What sealing buys is "the attacker has to write outside the repo (the guard
blocks the vault regardless of verb), and touching any of the other four files
gets caught." Fully removing it needs a separate user or container, beyond this
template's scope. Details in `docs/ci-verification.en.md` (the fourth round takes
over this loop's residual limit); the third round's original measurement is in the
round3 report P0-8 in history (retrieval in
[`docs/history/README.md`](docs/history/README.md), _Chinese_).

The plaintext is decrypted **under the vault** rather than in `/tmp`, so if a
process is force-killed (hit a usage cap, container reclaimed) and doesn't clean
up, the leftover at least lands in a path the guard covers; the next run,
`--sweep`, and every session's self-check clean it up and say so. This is
after-the-fact remediation, not prevention — between the kill and the next run,
the plaintext really is still there.

## 60-second quickstart

If you just want to get running without reading a wall of text, two steps:

```bash
git clone <repo-url> && cd <your-project>   # or copy harness-template/ contents in
python3 scripts/setup.py                     # setup wizard: detect language, generate config, init version
```

`setup.py` detects your language (Python / Node / Go / Rust), asks the few
questions that genuinely need your decision (protection level, whether to use CI
verification, whether there's a GUI, releases archiving), generates
`harness.config.json`, and finally **prints your next command** — paste it and
you run your first seal→verify. Don't want to be asked? Add `--yes` to use all
defaults.

Want to watch it run end to end without touching your project:
`templates/examples/demo-fizzbuzz/`.

Too heavy? `python3 scripts/setup.py --level minimal` turns on only layer 1
(physical isolation), with no hooks to install (see
[`docs/protection-levels.en.md`](docs/protection-levels.en.md)).

## Quick start (step by step)

> **Starting completely from scratch** (Python / Claude Code not installed yet,
> or applying to an existing project) — read
> **[`docs/getting-started.en.md`](docs/getting-started.en.md)**. Every step there
> comes with "how to confirm this step worked," because this template's biggest
> risk is **it looks installed but protection isn't actually in effect**. The
> section below assumes you already have a working environment and want to walk
> each step by hand (if you don't want to do it by hand, use `setup.py` above).

1. `git clone` this repo (or copy the `harness-template/` contents into your
   project root).

2. Run initialization (machine-capability scan → existing-service scan →
   environment-fingerprint creation/comparison):

   ```bash
   python3 scripts/init.py
   ```

   Results are only printed; it decides nothing for you. The three scan scripts
   and `init.py` all support `--json` (stdout is JSON only), for the Orchestrator:

   ```bash
   python3 scripts/init.py --json
   ```

   It already computes `max_parallel_agents` and `suggested_port_range`, so you
   don't re-derive them from prose. If you changed machines and confirmed it was
   deliberate, use `python3 scripts/env-guard.py --update` to set the current
   environment as the new baseline fingerprint.

3. **Non-Python projects**: you need `harness.config.json` to specify your test
   directories and test command, or the anti-cheat mechanism can't find your
   tests and there's effectively no protection (`guard-selfcheck.py` warns you at
   session start). **The easiest way is `python3 scripts/setup.py`** — it fills
   this in per language; to write it by hand, see examples in
   `docs/multi-language-support.md` _(Chinese)_.

4. **Create a version number** (`guard-selfcheck.py` reminds you at session start
   if there isn't one):

   ```bash
   python3 scripts/version.py --init
   ```

   The product the template produces needs a version number — without it, when a
   user reports "it broke" there's nothing to pin down which version. Where the
   version lives, and which other places it appears (README, a program's
   `--version`, package manifest), are declared in `harness.config.json`; see
   `docs/versioning.md` _(Chinese)_.

5. Open the project in Claude Code and let the `Orchestrator` (see
   `.claude/agents/orchestrator.md`) decompose your requirement per
   `docs/task-decomposition-guide.md` _(Chinese)_. Before dispatching it also does
   two things:

   ```bash
   python3 scripts/estimate-time.py --tasks doc:3,module:1 --rounds 2   # estimate
   python3 scripts/suggest-skills.py --role all --deliverable pdf       # which skills to install
   ```

6. Follow the order in `docs/implementer-verifier-workflow.md` _(Chinese)_:
   **verifier writes tests first → implementer then writes code → verifier
   verifies**.

7. When verification fails, look at the objective count before deciding whether
   to dispatch another round:

   ```bash
   python3 scripts/show-attempts.py --task-id <task_id>
   ```

   Consecutive failures usually mean **the spec is unclear**, not that the
   implementer isn't trying hard enough.

## Directory guide

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Global rules (golden rules, workflow overview) |
| `.claude/agents/` | Sub-agent definitions (1 Orchestrator, 3 implementers, 3 verifiers) |
| `.claude/commands/` | `/task-plan` `/task-dispatch` `/machine-check` slash commands (usage below) |
| `scripts/` | Scans, the anti-cheat mechanism itself, decision-support tools (see the two tables below) |
| `docs/` | Getting started, CI verification, protection levels, task decomposition, model/thinking assignment, implementer/verifier separation, multi-language support, memory management, token-cost strategy, root-cause analysis, user-report handling, versioning, frontend/GUI design defaults, time estimation, skill-install decisions |
| `templates/` | task-spec, verification report, acceptance-criteria mapping, user-report templates (with a full example under `examples/`) |
| `reports/` | Where verification reports land (the verifier has no `Write` tool; the Orchestrator files them) |
| `releases/` | One folder per version holding that version's full source (auto-created on bump, can be turned off; see `docs/versioning.md` §4.5, _Chinese_) |
| `VERSION` | This project's version number (location settable via `harness.config.json`; only `scripts/version.py` may write it) |
| `design.tokens.json` | Frontend/GUI design baseline: role-named colors, radii, spacing, type, transitions (projects with no GUI can disable via `"design": false`) |
| `.github/workflows/` | CI: `ci.yml` runs the template's own regression tests; `verify-hidden-tests.yml` is the **real verification of hidden tests** (see `docs/ci-verification.en.md`) |
| `ci/sealed/` | The sealed bundle for CI verification (one folder per task, containing ciphertext + the signed manifest entry). Version-controlled, and must be committed to the **default branch** to take effect |
| `harness.config.json` | (Optional) this project's test-path convention, version source and display locations, **protection level**; non-Python projects must set it |
| `skills.catalog.json` | (Optional) which skills this project installs, and for whom |

### When to use the three slash commands

| Command | When | What it does |
|---|---|---|
| `/task-plan` | **When there's no plan yet** — you only have a one-line requirement | clarify → scan → decompose → assign models, estimate time, decide skills, produce a plan awaiting approval |
| `/task-dispatch` | **After the plan is approved** | dispatch per the plan: verifier writes tests and seals first, then the implementer starts |
| `/machine-check` | You just want to see machine state, without triggering decomposition | run the three scans (machine capability, existing services, environment fingerprint) alone |

The order is fixed: `/task-plan`'s output is `/task-dispatch`'s input. Skipping
the former and dispatching directly is handing the implementer a spec with no
scope boundary — the most expensive way to fail in the whole flow (see
`docs/token-strategy.md` §2.1, _Chinese_).

## What the anti-cheat mechanism is made of

| Script | Role | When it runs |
|---|---|---|
| `scripts/seal-hidden-tests.py` | **Physical isolation**: lock public tests → encrypt each staged hidden test (per-file key) and move it outside the repo → **copy the verification code into the vault and record its sha256** → sign the manifest with the token, producing a one-time execution token. Refuses to seal files already in git history | after verifier-test-writer finishes writing tests |
| `scripts/run-hidden-tests.py` | The **only** entry point to run hidden tests: verify manifest signature → compare sealed code sha256 → compare locked-list sha256 → decrypt and run. **Verification must run the copy in the vault** (`<vault>/_runner/`, copied by seal, absolute path relayed by the Orchestrator); the in-repo copy is editable by the implementer. `--baseline` proves the tests are red without the implementation; normal mode records signed attempt counts | after test-writer seals (`--baseline`); when verifier-reviewer verifies |
| `scripts/discard-sealed-task.py` | **The only exit after a lost token**: move the now-undecryptable ciphertext into the vault's `_discarded/` (not delete — it leaves evidence that "this task existed" which takes deliberate action to erase), clear leftover plaintext, leave a tombstone in the manifest, append a line to the seal ledger outside the repo, and point to the rewrite flow. Not a rescue path — the discard count is carried into the re-sealed, signed entry, so history can't be laundered | when the token vanishes with its session (most common: hit a usage cap while running tests) |
| `scripts/export-sealed-task.py` | **Step one of moving verification off this machine**: copy the vault's ciphertext into the in-repo `ci/sealed/<task_id>/`, with the signed manifest entry. No decrypt, no re-sign — so the export action itself needs no trust | after sealing (auto-run by seal when `ci_verification` is declared) |
| `scripts/ci-verify.py` | **The verification entry on CI**: verify signature → decrypt outside the tested directory → run tests. The job log has only exit code and count summary; **failure details never go into the log** (anyone with repo read access sees the log; printing them turns the hidden tests into a queryable oracle); the full detail is encrypted into an artifact. The child process gets neither the token nor CI tokens | GitHub Actions, launched by `verify-hidden-tests.yml` |
| `scripts/read-ci-report.py` | Decrypt the CI-produced encrypted detail with the token. A wrong token, or tampered detail, is rejected rather than handed back as a fake report | when verifier-reviewer verifies |
| `scripts/hidden_vault.py` | The shared vault logic the above use (encryption, manifest, path rules, CI sealed bundle, zero-test detection) | imported, not run directly |
| `scripts/harness_config.py` | Reads `harness.config.json`: this project's test-path convention and **version source** (non-Python projects must set it) | imported, not run directly |
| `scripts/version.py` | **The produced project's version number**: read/write semver, supporting plain text / `package.json` / `pyproject.toml`, writing back without breaking the rest of the file. `mirrors` updates and checks the version across README/code/manifest together. On bump, archives the source to `releases/<version>/`. Only the Orchestrator uses it; implementer writes to the version file and archive are blocked by the guard (reads aren't) | confirm version before decomposing; bump after a batch of requirements is verified |
| `scripts/release_archive.py` | The version-archive body: one folder per version holding full source. **The hidden-test staging area is always excluded** (putting it in would leak from the archive), and the archive dir excludes itself to avoid recursion | called by `version.py`, not run directly |
| `scripts/guard-hidden-tests.py` | **Pre-interception**: a PreToolUse hook that blocks access to the staging area, the vault, and `.harness/` (except `progress/`); for locked public tests, the version file, `releases/`, and — **after this repo has sealed a task** — the enforcement body (`scripts/`, `.claude/`, `CLAUDE.md`, `harness.config.json`), it **blocks writes only, not reads** (reading them is legitimate) | every tool call (mounted by `.claude/settings.json`) |
| `scripts/lock-tests.py` | Writes the public tests' **path + sha256** into `.harness/locked-tests.list`; the list's sha256 gets signed into the manifest by seal | auto-called by `seal-hidden-tests.py`; editing public tests after sealing means re-sealing, not just re-running this |
| `scripts/verify-locks.py` | **After-the-fact audit**: recompute hashes and compare, catching tampering that "bypassed pre-interception." `guard-selfcheck.py` **actually runs** it once per session with known scenarios to confirm it wasn't swapped | first step of verifier-reviewer's verification |
| `scripts/guard-selfcheck.py` | **Self-check**: actually run a known-should-be-blocked payload once; scan for directories that "look like hidden tests but aren't in a protected path"; clean up decryption leftovers from a previous force-kill; report the current version and whether it's consistent everywhere, and whether any task was discarded due to a lost token | SessionStart hook |

The four layers each block something different (`protection.level` can turn on
only layer 1, see [`docs/protection-levels.en.md`](docs/protection-levels.en.md)):

1. **Physical isolation** (sealing): files aren't in the working tree and the
   content is ciphertext — this is the main line of defense.
2. **Pre-interception** (hook): a Bash command is blocked if the staging or vault
   path is **parseable** out of it, regardless of verb (`bash -c`, `find`,
   `xargs`, `tar`, `$(…)`, variable expansion all covered); obfuscation that
   writes no path at all is out of this layer's scope — layer 1 blocks that.
3. **After-the-fact audit** (hashing): doesn't depend on interception succeeding;
   if it was changed, it's detectable.
4. **Self-check** (SessionStart): when the mechanism breaks, there's at least a
   signal instead of silently opening everything up.

**CI verification is not a 5th layer, it's a different axis.** The four layers
above all ask "how to make bypass harder on this machine," and their shared
ceiling is: the one who bypasses runs under the same OS user as verification.
Moving verification to GitHub Actions removes that premise — the workflow and
sealed bundle come from the default branch, and nothing the implementer can
change on their own branch participates in verification. Its own new roots of
trust (branch protection, GitHub's secret) and residual limits are in
[`docs/ci-verification.en.md`](docs/ci-verification.en.md).

> **One thing repo maintainers will hit**: layer 2 is "if the command string
> mentions that path, block it," so even a Bash heredoc that "wants to write the
> path into some file's content" gets blocked. Use the Write/Edit tools for that
> — they look only at the target file, not the content. This is a deliberate
> trade-off: over-blocking is safe, under-blocking is dangerous.

## Decision-support tools

These aren't protection; they let the Orchestrator decide by numbers instead of
feel:

| Script | Role | When it runs |
|---|---|---|
| `scripts/capacity.py` | Compute a parallelism **ceiling** from CPU/memory, pick unoccupied port ranges (pure functions) | imported, not run directly |
| `scripts/scan_cache.py` | Scan-result cache (`.harness/last-scan.json`); if the capability fingerprint mismatches, reuse is denied | imported, not run directly |
| `scripts/timing.py` / `scripts/estimate-time.py` | Runtime estimation: per-class unit cost + cycle overhead + CI retries, calibrated by project history | estimate after decomposing, backfill actuals after verifying |
| `scripts/skill_policy.py` / `scripts/suggest-skills.py` | Decide which skills to install per project goal; enforce "the verifier doesn't get code-generating skills" | before dispatch |
| `scripts/attempts.py` / `scripts/show-attempts.py` | Attempt count and stop-loss decision; the count is written by the runner, not self-reported by the implementer | after verifying, when deciding whether to dispatch another round |
| `scripts/design_tokens.py` / `scripts/check-design-tokens.py` | Frontend/GUI design baseline: validate token structure and compute WCAG AA contrast per pair for both light and dark. Whether a design looks good can't be verified, but whether it's readable can | when verifying a frontend task, and after changing any color |

Three shared design principles (each detailed in the respective script):

- **Always give a range or a ceiling, never a single confident-looking number.**
  `max_parallel_agents` is the machine's capacity ceiling, not a recommendation
  (sequential by default); estimates always return a range.
- **When information is incomplete, say "don't know," not "fine."** When the port
  scan is incomplete, `suggested_port_range` is `null`; when the attempt-count
  file is broken, `should_stop` is `null` rather than `false` — "can't read" and
  "never failed" are two different things.
- **Broken config always errors, never silently falls back to defaults.** Being
  able to turn off protection with one typo, with no signal, is the least
  acceptable failure mode in this repo.

## Tests and CI

After changing any script, run:

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py \
  && python3 scripts/test-vault.py && python3 scripts/test-env-guard.py \
  && python3 scripts/test-config.py && python3 scripts/test-scan-json.py \
  && python3 scripts/test-timing.py && python3 scripts/test-skills.py \
  && python3 scripts/test-attempts.py && python3 scripts/test-version.py \
  && python3 scripts/test-design.py && python3 scripts/test-ci-verify.py \
  && python3 scripts/test-protection-levels.py && python3 scripts/test-setup.py
```

These fourteen suites (**466 cases** total) also run in CI across
**Linux / macOS / Windows × Python 3.9 / 3.13**, with Linux doing one extra pass
under `LC_ALL=C` (non-UTF-8 locale) (see `.github/workflows/ci.yml`).

This repo especially needs CI, because **mechanism degradation is silent** — the
guard missing one path form, the seal script missing one file to delete: it all
looks fine, only the tests notice. CI caught, the moment it was enabled, a
long-standing but never-noticed bug: every script crashed the instant it printed
Chinese to a Windows console.

Supported Python: **3.9+**, with no third-party packages required (`psutil` is
optional; installing it makes the service scan more accurate).

Windows users don't need to set `PYTHONUTF8`: every script switches stdout/stderr
to UTF-8 at startup (see `scripts/utf8_output.py`), otherwise the system ANSI
code page can't encode the Chinese output and the script crashes outright.

> A cross-platform practical limit: use ASCII for `task_id` (e.g. `T-012`). Under
> a locale like `LC_ALL=C`, non-ASCII command-line arguments can't be encoded at
> the OS level — unrelated to this template. Put Chinese in the task-spec title.

## Token cost

One task's fixed overhead in this template is roughly 56K characters — three
sub-agent sessions with no shared context each reload `CLAUDE.md` and their own
definition file. That's the necessary price of an "independent verification
chain," but knowing where the money goes tells you where to save.

The three highest-leverage things: **write the task-spec's scope boundary
clearly** (without it the implementer greps the code itself, the most
uncontrollable cost), **verify low-risk tasks small-then-big**, and **keep
`CLAUDE.md` to rules and pointers only** (its multiplier is highest).

Subscription plans (e.g. Pro) have one more thing to watch: **what blocks you is
the burn rate, not the dollar amount**. The cost of hitting a usage cap
compounds — wait, session drops, reload one fixed overhead, then hit the next cap
sooner. So on such plans it's **sequential by default**, commit after each task,
and write progress into `.harness/progress/<task_id>.md` so recovery after an
interruption costs "read one small file" instead of "reconstruct the whole line
of reasoning."

For measurements, the full subscription-plan strategy, the list of what not to
save, and the common misconception that "parallelism saves tokens," see
`docs/token-strategy.md` _(Chinese)_.

## Design basis

This template borrows the "Agent Teams" topology-decision framework from
[claude-code-ultimate-guide-zh](https://github.com/JAYcodr/claude-code-ultimate-guide-zh),
and the **SE-CoVe (independent verification chain, Meta AI, ACL 2024)** concept it
mentions, splitting "produce the answer" and "verify the answer" into two fully
independent chains.

This template went through four rounds of adversarial review, each asking "what
makes the previous round's guarantee hold." The four full reports (~114 KB,
human-only) have been **moved off the main line** — keeping them in `docs/` only
gives every cloner one more piece of reasoning they won't read, and turns into a
living document that gradually drifts from the code (which is exactly the final
application of that rule in `docs/token-strategy.md` §1). The 200-word decision
summary and "how to retrieve the full reports from git history" are in
`docs/history/README.md` _(Chinese)_.

The fourth round's conclusion is **this axis should stop**: the loop the third
round recorded itself ("swap out the part that checks itself" and nobody runs
that check) is not an item still to be fixed, it's the structural endpoint of
self-checking. So this round doesn't add a fifth layer — it moves verification to
GitHub Actions ([`docs/ci-verification.en.md`](docs/ci-verification.en.md)) and
splits "four layers all on or don't use it" into optional protection levels
([`docs/protection-levels.en.md`](docs/protection-levels.en.md)). The residual
limits and new roots of trust are written in those two docs, with no
whitewashing.
