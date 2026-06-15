# Test: `/newtool` — ExTool generator (toolsmith + validator)

A step-by-step runbook to confirm the tool generator works end to end. A ready
sample subject ships with the repo, so you can run this immediately.

**Subject (already created for you):**
`ExTools/wordcount/wordcount_repo/wordcount.py` — a tiny library with
`count_words`, `count_chars`, `count_lines`.

**What success looks like:** `/newtool` produces a valid `ExTools/wordcount/__init__.py`,
the validator passes, and the registration stanza is printed — **without** any
config file being edited automatically.

---

## Part A — Validator (offline, deterministic, no API key)

This proves the safety-net the agent relies on. Run from the repo root
(`C:\thinkbig\Anet\Anet`):

**Step A1 — validate a known-good tool**
```
python -m anet.core.extool_validator ExTools/tele_tool/__init__.py
```
✅ Expect: several `OK:` lines, then `PASS: ... is a valid ExTool.` (exit code 0).

**Step A2 — run the validator unit tests**
```
python tests/AnetToolTests/extool_validator/test.py
```
✅ Expect: six `ok  test_...` lines, then `PASS: extool_validator`.

If A1/A2 fail, stop here — the generator can't be trusted until the validator works.

---

## Part B — Live generation with `/newtool` (needs an API key)

Uses your configured manager model (`anet.config.yaml`). Make sure that provider's
key is in `.env`.

**Step B1 — start ANet**
```
python main.py
```

**Step B2 — confirm the subject exists** (optional sanity check, in another shell)
```
dir ExTools\wordcount\wordcount_repo
```
✅ Expect: `wordcount.py`.

**Step B3 — run the generator**
At the `You:` prompt, type:
```
/newtool ExTools/wordcount/wordcount_repo
```

**Step B4 — answer the confirmation**
The `toolsmith` agent explores the code, then asks you (via ask_user) to confirm
the tool name and what to expose. Either accept its proposal or reply, e.g.:
```
name it wordcount, expose count_words with a single "text" string param
```

**Step B5 — approve the file write**
It writes `ExTools/wordcount/__init__.py`. You'll see a unified diff and a `y/n/a`
prompt → press **y**.

**Step B6 — approve the validation run(s)**
It runs `python -m anet.core.extool_validator ExTools/wordcount/__init__.py` via the
shell (another `y/n/a`) → press **y**. If the validator prints any `FAIL`, the agent
reads it, edits the file, and re-validates. Approve those steps too.

**Step B7 — read the final message**
✅ Expect a summary plus a yaml code block like:
```yaml
tools:
  - name: wordcount
    path: ExTools/wordcount
```

### Pass criteria
- [ ] `ExTools/wordcount/__init__.py` now exists.
- [ ] Running `python -m anet.core.extool_validator ExTools/wordcount/__init__.py`
      prints **PASS**.
- [ ] The final message printed the `exanet.config.yaml` stanza.
- [ ] `exanet.config.yaml` was **NOT** modified (still `tools: []`). Verify:
      `findstr /C:"wordcount" exanet.config.yaml` returns nothing.

---

## Part C — Actually use the generated tool (optional, proves the whole loop)

**Step C1 — register it.** In `exanet.config.yaml`:
```yaml
tools:
  - name: wordcount
    path: ExTools/wordcount
```
**Step C2 — give it to an agent.** In `anet.config.yaml`:
```yaml
agents:
  code_agent:
    extra_tools: [wordcount]
```
**Step C3 — restart ANet** (`extra_tools` on a built-in is not hot-reloaded), then ask:
```
use the wordcount tool to count the words in: the quick brown fox jumps
```
✅ Expect the agent to call `wordcount` and answer **5**.

---

## Reset (to re-run cleanly)
```
del ExTools\wordcount\__init__.py
```
(Leave `wordcount_repo/` — it's the reusable subject.)

## Troubleshooting
- **"API key not set"** → the manager provider's key is missing in `.env`.
- **Validator loops without reaching PASS** → check the `FAIL` reason; usually the
  wrapped import path is wrong (the generated file should `sys.path.insert` the
  `wordcount_repo` folder relative to `__file__`) or a required param isn't in
  `properties`. The agent should fix these itself within ~5 tries.
- **It tried to edit a config file** → that's a bug; the agent is instructed to
  only *print* the stanza. Note it in `RESULTS.md`.
