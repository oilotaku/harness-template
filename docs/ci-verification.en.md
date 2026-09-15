<!-- 語言 / Language: [繁體中文](ci-verification.md) · **English** -->

# CI verification: move verification out of the implementer's environment

> Corresponds to round four, P0. This is the only change in this template so far
> that is **not adding a layer** — the first three rounds were all on the same
> axis (sign one more thing, seal one more thing); this round changes the axis.
>
> English translation of [`ci-verification.md`](ci-verification.md); the Chinese
> version is the source of truth.

## 1. Why adding layers no longer pays off

The third round's honest record said this:

> If what gets swapped is the sealed `run-hidden-tests.py` itself, and the
> swapped version removes the self-comparison entirely, then nobody runs that
> check — every self-check has this loop.

This is not "an item still to be fixed," it's **that axis's structural
endpoint**. As long as verification and the verified run on the same machine,
under the same OS user, it always stops at the same place: the checking code
lives in the same writable world as the side being checked.

So the fourth round doesn't add a fifth layer. It moves verification somewhere
the implementer's tools can't reach: **GitHub Actions**. A different machine, a
different user, no shared transcript, and the workflow file comes from the
default branch — this time, the "swap out the copy that checks itself" move
doesn't exist from the start.

## 2. How it works

```
verifier-test-writer
  └─ seal-hidden-tests.py            ← encrypted seal (same as before)
       └─ export-sealed-task.py      ← export the sealed bundle to ci/sealed/<task_id>/
            │                           (*.enc + the signed manifest entry)
            ▼
       human: commit ci/sealed/ to the **default branch**
       human: store the token in repository secret `HARNESS_VERIFY_TOKENS`
            │
            ▼
implementer implements on their own branch → opens a PR
            │
            ▼
.github/workflows/verify-hidden-tests.yml (**the copy on the default branch**)
  ├─ checkout base (default branch) → base/   ← trusted: sealed bundle + verification scripts
  ├─ checkout head (PR branch) → work/        ← the thing under test, no checkout credentials
  └─ python base/scripts/ci-verify.py --repo base --work-dir work
       ├─ verify token fingerprint, verify manifest signature
       ├─ decrypt into a temp dir outside work/
       ├─ run tests (the child process gets neither the token nor CI tokens)
       ├─ job log: only exit code and count summary
       └─ report.enc: full detail, encrypted with the token into an artifact
            │
            ▼
verifier-reviewer: download the artifact → read-ci-report.py --token <token>
```

Three things determine its strength, none optional:

1. **`pull_request_target`**: GitHub runs the workflow file on the default
   branch, not the one on the PR branch. Changing it to `pull_request` collapses
   the whole design — that would let the implementer pass verification with a
   one-line `run: exit 0`.
2. **The sealed bundle and the verification scripts both come from the base
   checkout.** The PR's code is only the thing under test; it supplies no
   verification logic that gets executed.
3. **The token is a repository secret and does not flow into the tested code's
   environment** (`scrub_env` in `scripts/ci-verify.py`, guarded by regression
   tests).

## 3. How to set it up

### 3.1 Declare

`harness.config.json`:

```json
{
  "protection": { "ci_verification": true }
}
```

Once declared, `seal-hidden-tests.py` exports the sealed bundle after sealing,
and `guard-selfcheck.py` warns when "CI verification is declared but the sealed
bundle is empty" — the danger of that drift is that it's silent: CI would return
success because there's "nothing to verify," which looks identical to "all
passed."

### 3.2 Per task

```bash
# 1. Seal (with ci_verification declared, step 2 is done automatically)
python3 scripts/seal-hidden-tests.py --task-id T-001

# 2. Export the sealed bundle (when doing it manually)
python3 scripts/export-sealed-task.py --task-id T-001 --token <token>

# 3. Commit the sealed bundle to the default branch
git add ci/sealed/T-001 && git commit -m "Seal T-001's hidden-test bundle"
```

### 3.3 Token

Store it as a repository secret named `HARNESS_VERIFY_TOKENS`, one task per line:

```
T-001 3f9a...c2
T-002 8b1d...07
```

Location: Settings → Secrets and variables → Actions → New repository secret.
Updating is a **full overwrite**, so remember to carry the other tasks' lines
along.

### 3.4 Branch protection

**This step is not optional.** The whole design's root of trust is "the workflow
and sealed bundle on the default branch, which the implementer can't touch." If
anyone can push directly to the default branch, that sentence doesn't hold. At a
minimum, enable: no direct push to the default branch, PR required before merge.

## 4. What the new root of trust is

Better to say it plainly: this approach doesn't eliminate trust, it **swaps what
it's placed on**.

| Trusted before | Trusted now |
|---|---|
| the sealed runner's sha256 self-comparison | GitHub runs the workflow on the default branch |
| "the implementer can't write outside the repo" | "the implementer can't push to the default branch" (branch protection) |
| the local filesystem's permissions | GitHub's secret storage |

Is the swap worth it? Yes, because **none of these trusted objects is within the
implementer's tool reach**, whereas the previous round's were. They have Bash, so
they can write any local path; they don't have push access to the default branch,
nor repo admin.

## 5. What it blocks and what it doesn't (the honest version)

### What it blocks

- **Rewriting the verification code**: verification runs the base checkout's
  `scripts/ci-verify.py`. Whatever the PR-branch copy becomes doesn't
  participate.
- **Rewriting the workflow**: `pull_request_target` reads the default-branch
  copy.
- **Rewriting the sealed bundle or manifest entry**: the signature is derived
  from the token, which the implementer doesn't have.
- **Reverse-engineering the tests from the job log**: the log has only exit code
  and counts; the detail is encrypted into an artifact. This one is watched by a
  regression test (failure assert messages must not appear in stdout/stderr).
- **A fork PR stealing the token**: `pull_request_target` still hands the secret
  to fork PRs, so the workflow restricts to same-origin branches — fork PRs
  simply don't run verification.

### What it doesn't block (don't treat as solved)

- **The code under test and the decrypted plaintext are in the same job.** It has
  to be able to reach those tests by nature, so it can read them and can send the
  contents outside. The CI runner has full outbound network.

  What this buys is not "can't be done," it's **"doing it leaves a commit"**: that
  exfiltration code has to exist in the PR's diff, seen by review and permanently
  recorded by git. Local verification doesn't have this property — locally, after
  doing the same thing you can delete the file and nothing remains.

- **Whoever can change the default branch = can get the token.** Add a workflow
  that prints the secret. This isn't a flaw in the mechanism, it's a basic
  property of GitHub Actions: **secret security equals default-branch write
  security**. So §3.4's branch protection is a required step, not a suggestion.

- **The repo admin can get the token too.** They can change branch protection. If
  your threat model includes "the repo owner cheats," CI verification can't solve
  it — that needs a third party they don't control.

- **Verification gets slower.** From "run one command, a few seconds" to "push
  once, wait for CI, download artifact, decrypt." A round is a few minutes more.
  This is a real cost; don't pretend it isn't.

- **The secret needs managing.** One line per task, cleaned up when the task is
  done. Forgetting to clean up isn't a security problem (that task's sealed
  bundle just takes up space), but it grows.

- **The local `run-hidden-tests.py` can still be rewritten.** Its role now is
  "the verifier's second opinion," not the verification basis. All the third
  round's protections (sealed-copy execution, sha256 comparison) are still there,
  but the authoritative source of verification is now CI.

## 6. Relationship to local verification

With CI verification on, the local `run-hidden-tests.py` doesn't disappear, it
changes role:

| | Local | CI |
|---|---|---|
| Who runs it | verifier-test-writer (baseline), verifier-reviewer (second opinion) | GitHub Actions |
| Is it the real verification | no | **yes** |
| Can the implementer influence it | yes (same machine, same user) | no |
| Feedback speed | seconds | minutes |

The baseline run (`--baseline`) still happens locally: it proves "this hidden
test is red without the implementation," at a time when there's no implementer
yet and no adversarial relationship.

The verification report should state the CI run's result and note the workflow
run id. The detail decrypted by `read-ci-report.py` **must not** be pasted into
the report — it contains the hidden tests' failure messages, and the report is
readable by the implementer.
