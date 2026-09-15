<!-- 語言 / Language: [繁體中文](protection-levels.md) · **English** -->

# Protection levels: minimal and full

> Corresponds to round four, P1. This document is about the `protection` block in
> `harness.config.json`: how many layers of protection this project turns on, and
> where verification runs.
>
> English translation of [`protection-levels.md`](protection-levels.md); the
> Chinese version is the source of truth.

## 1. Why this option exists

Before this, the mechanism was **all or nothing**: four layers on together, and
four layers are too heavy for most projects — you install hooks, maintain the
discipline that "public tests can't change once locked," and get an extra
self-check every session. So what actually happened: people who found it too
heavy didn't use any of it, rather than using one layer.

But the only truly irreplaceable one of the four is layer 1. **Physical
isolation** (encrypted seal outside the repo + execution token + manifest
signature) is by itself enough to make "the implementer can't see the hidden
tests" hold, and it needs no hooks and no Claude Code config — three scripts run
it.

The other three layers buy **tamper-resistance** and **signals**, not isolation
itself. Worth it, but not every project needs them, and their cost is discipline
cost, not install cost — and discipline cost is the kind that gets bypassed.

## 2. The two levels

```json
{
  "protection": { "level": "minimal" }
}
```

| | `minimal` | `full` (default) |
|---|---|---|
| 1. Physical isolation (seal + encrypt + token + signature) | ✅ | ✅ |
| 2. Pre-interception (PreToolUse hook) | ❌ | ✅ |
| 3. Public-test locking + after-the-fact audit | ❌ | ✅ |
| 4. SessionStart self-check probes | ❌ | ✅ |
| Config to change | 3 lines of `harness.config.json` | plus the hook in `.claude/settings.json` |
| Scripts used | `seal-hidden-tests.py`, `run-hidden-tests.py`, `discard-sealed-task.py` | all |

What `minimal` still does — nothing dropped:

- Hidden tests sealed outside the repo, per-file encryption key.
- One-time execution token; a wrong token can't decrypt.
- Manifest entry signed with the token; edit `test_command` or such and it won't
  run.
- The verification code is sealed too, its sha256 in the signature scope.
- Baseline run (proving the hidden tests are red without the implementation).
- Zero-test false-pass detection.
- Verification attempt count and stop-loss decision.

## 3. What minimal gives up (the honest version)

**Public tests being changed won't be caught.** This is the main one. Layer 3's
after-the-fact audit (`verify-locks.py`) works by "recording the public tests'
sha256 into the signed manifest at seal time"; minimal doesn't lock, so that
baseline doesn't exist. An implementer changing a public test to `assert True`
produces no signal.

**No pre-interception.** No hook, so a reflexive `cat tests/hidden/...` isn't
blocked — but that directory is empty after sealing, so what they see is "no
files." The real difference is **before** sealing: during the window while
verifier-test-writer writes tests and hasn't run seal yet, the plaintext is in
the working tree. **So minimal has one extra operational discipline: seal
immediately after writing tests, don't leave them in staging overnight.**

**No self-check probes.** When the mechanism breaks there's no signal. But
minimal removes most of what can break — layer 1 breaks by "the seal script fails
to run," which is visible.

## 4. Which layers are off gets said out loud, every time

This is the most important part of minimal's design: **it is not a silent skip of
a few checks.**

- `seal-hidden-tests.py` prints at seal time "this won't lock public tests."
- `run-hidden-tests.py` prints at verify time "no public-test locking or audit
  this time," and asks you to write that into the verification report.
- `guard-selfcheck.py` prints the protection level and the three off layers every
  session.

The reason is the same as everywhere else in this template: protection one layer
short **with no signal** is more dangerous than no protection — at least with the
latter nobody wrongly believes they're protected.

Under `minimal`, the SessionStart probes **don't run**, deliberately: without
hooks installed the probes would all fail, and printing a wall of red every
session only trains the "ignore warnings" habit, which is harder to fix than one
missing layer.

## 5. When to use which

Use `minimal`:

- Your solo project, where the implementer is a sub-agent you dispatched
  yourself, with no adversarial motive.
- You want to try the flow first without touching `.claude/settings.json`.
- The project is exploratory and public tests change daily.

Use `full`:

- Someone makes decisions based on the verification result (delivery, billing,
  merging).
- Bug-fix verification (`--kind bugfix`) — the most common fake fix there is
  "make only the reported case pass," and catching it needs public-test locking.
- You plan to dispatch repeatedly against the same spec long-term.

Upgrading anytime: change `level` to `full` (or delete the whole `protection`
block), install the hook, and re-seal once. Re-sealing locks the current public
tests, and the baseline is established.

## 6. CI verification is a different axis, not a third level

`protection.ci_verification` and `level` are **two independent choices**:

```json
{
  "protection": { "level": "minimal", "ci_verification": true }
}
```

`level` decides "how many layers on locally," `ci_verification` decides "where
the real verification runs." `minimal` + CI verification is a reasonable, even
quite strong, combination: local does isolation only, and the real verification
runs somewhere the implementer can't touch at all. See
[`ci-verification.en.md`](ci-verification.en.md).
