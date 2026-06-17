"""
skill_manager.py — self-improving skills system for ANet.

Skills are markdown procedure files in skills/ that agents write for
themselves after complex tasks and read before relevant new ones.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]


# ── Config helpers ─────────────────────────────────────────────────────────────

def _cfg() -> dict:
    try:
        from anet.core.config_loader import load
        return load().get("skills", {})
    except Exception:
        return {}


def _skills_dir() -> Path | None:
    c = _cfg()
    if not c.get("enabled", True):
        return None
    return _REPO_ROOT / c.get("skills_dir", "skills")


def _max_injected()         -> int: return int(_cfg().get("max_injected",          3))
def _creation_threshold()   -> int: return int(_cfg().get("creation_threshold",    6))
def _curator_min_skills()   -> int: return int(_cfg().get("curator_min_skills",    5))
def _stale_after_days()     -> int: return int(_cfg().get("stale_after_days",      30))
def _curator_interval_hrs() -> int: return int(_cfg().get("curator_interval_hours", 12))


# ── Usage telemetry + provenance (sidecar) ─────────────────────────────────────
#
# Lifecycle metadata lives in skills/.usage.json, keyed by skill stem — kept out
# of the markdown so curator decisions never churn user-visible content. A skill
# is only eligible for automatic curation (archive / merge / improve) when its
# record is marked created_by == "agent". Skills with no record, or marked
# created_by == "user", are left untouched.

def _usage_path() -> Path | None:
    sdir = _skills_dir()
    return (sdir / ".usage.json") if sdir else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    """Write text via temp file + os.replace so a crash never leaves a
    half-written (corrupt) file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_usage() -> dict:
    p = _usage_path()
    if not p or not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_usage(data: dict) -> None:
    p = _usage_path()
    if not p:
        return
    try:
        _atomic_write(p, json.dumps(data, indent=2, sort_keys=True))
    except Exception as exc:
        print(f"[skill_manager] could not write usage sidecar: {exc}", file=sys.stderr)


def _empty_record() -> dict:
    return {
        "created_by":   None,       # "agent" → curator-managed; None/"user" → off-limits
        "created_at":   _now_iso(),
        "use_count":    0,
        "last_used_at": None,
        "pinned":       False,
        "state":        "active",   # active | archived
    }


def get_usage_record(stem: str) -> dict:
    rec = _load_usage().get(stem)
    if not isinstance(rec, dict):
        return _empty_record()
    base = _empty_record()
    base.update(rec)
    return base


def record_skill_created(stem: str, by: str = "agent") -> None:
    data = _load_usage()
    rec = data.get(stem) if isinstance(data.get(stem), dict) else _empty_record()
    rec["created_by"] = by
    rec.setdefault("created_at", _now_iso())
    data[stem] = rec
    _save_usage(data)


def record_skill_used(stem: str) -> None:
    data = _load_usage()
    rec = data.get(stem) if isinstance(data.get(stem), dict) else _empty_record()
    rec["use_count"]    = int(rec.get("use_count") or 0) + 1
    rec["last_used_at"] = _now_iso()
    data[stem] = rec
    _save_usage(data)


def forget_usage(stem: str) -> None:
    data = _load_usage()
    if stem in data:
        del data[stem]
        _save_usage(data)


def is_pinned(stem: str) -> bool:
    return bool(get_usage_record(stem).get("pinned"))


def set_pinned(stem: str, pinned: bool) -> bool:
    """Pin/unpin a skill so the curator never archives, merges, or rewrites it.
    Returns False if the skill file does not exist."""
    sdir = _skills_dir()
    if not sdir or not (sdir / f"{stem}.md").exists():
        return False
    data = _load_usage()
    rec = data.get(stem) if isinstance(data.get(stem), dict) else _empty_record()
    # Pinning a previously unmarked skill implies the user wants to keep it —
    # mark it agent-created so the record is complete, but pin protects it anyway.
    rec.setdefault("created_by", "agent")
    rec["pinned"] = bool(pinned)
    data[stem] = rec
    _save_usage(data)
    return True


# ── Skill file helpers ─────────────────────────────────────────────────────────

_STOP = {
    'with', 'that', 'this', 'from', 'have', 'will', 'your', 'also', 'into',
    'then', 'them', 'they', 'what', 'when', 'where', 'which', 'there', 'their',
    'about', 'would', 'could', 'should', 'after', 'before', 'being', 'been',
    'make', 'need', 'want', 'just', 'more', 'some', 'does', 'done', 'used',
    'each', 'file', 'call', 'code', 'task', 'step', 'next', 'only',
}


def _keywords(text: str) -> list[str]:
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= 10:
            break
    return out


def read_skill_header(path: Path) -> tuple[str, str, int]:
    """Return (stem, applies_to, used_count)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        applies_to = ""
        used = 0
        for ln in lines[:15]:
            if ln.startswith("**Applies to:**"):
                applies_to = ln.replace("**Applies to:**", "").strip()
            if ln.startswith("**Used:**"):
                try:
                    used = int(ln.replace("**Used:**", "").strip())
                except ValueError:
                    pass
        return path.stem, applies_to, used
    except Exception:
        return path.stem, "", 0


def _increment_used(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
        m = re.search(r'\*\*Used:\*\*\s*(\d+)', content)
        if m:
            new_val = int(m.group(1)) + 1
            content = content[:m.start()] + f"**Used:** {new_val}" + content[m.end():]
            _atomic_write(path, content)
    except Exception:
        pass


def _safe_name(text: str) -> str:
    """Turn the first line of LLM output into a safe filename stem."""
    first = text.splitlines()[0] if text else "skill"
    name  = re.sub(r'^#+\s*', '', first).strip()
    name  = re.sub(r'[^a-zA-Z0-9_\- ]', '', name)
    name  = name.replace(" ", "_").lower()[:40] or "skill"
    return name


# ── Part 2 — Skill injection ───────────────────────────────────────────────────

def find_relevant_skills(task_text: str) -> str:
    """
    Keyword-search skills/ for files relevant to task_text.
    Returns a markdown injection block (empty string if nothing matches).
    Increments Used count for injected skills.
    """
    sdir = _skills_dir()
    if not sdir or not sdir.exists():
        return ""

    kws = _keywords(task_text)
    if not kws:
        return ""

    skill_files = [f for f in sdir.glob("*.md") if f.is_file()]
    if not skill_files:
        return ""

    matches: list[tuple[int, Path, str]] = []
    for sf in skill_files:
        try:
            content = sf.read_text(encoding="utf-8")
        except Exception:
            continue
        # Match against filename + first 3 lines
        header = sf.stem.replace("_", " ") + " " + "\n".join(content.splitlines()[:3])
        score  = sum(1 for kw in kws if kw in header.lower())
        if score > 0:
            matches.append((score, sf, content))

    if not matches:
        return ""

    matches.sort(key=lambda x: x[0], reverse=True)
    top = matches[:_max_injected()]

    for _, path, _ in top:
        _increment_used(path)
        record_skill_used(path.stem)

    block  = "## Relevant Skills from Past Experience\n\n"
    block += "\n\n---\n\n".join(content for _, _, content in top)
    return block


# ── Model caller (shared by creation + curator) ────────────────────────────────

async def _model_call(messages: list[dict], max_tokens: int = 800) -> str:
    try:
        from anet.core.config_loader import load as _lcfg
        mgr      = _lcfg().get("manager", {})
        model    = mgr.get("model",    "gemini-2.5-flash")
        provider = mgr.get("provider", "openrouter")

        from anet.core.agent_runner import (
            build_vertex_client, _build_openai_client, _PROVIDERS, _DEFAULT_PROVIDER,
        )
        if provider in ("vertex_google", "vertex_anthropic", "vertex_claude"):
            client = build_vertex_client()
        elif provider in _PROVIDERS:
            client = _build_openai_client(provider)
        else:
            client = _build_openai_client(_DEFAULT_PROVIDER)
            model  = "gemini-2.5-flash"

        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        print(f"[skill_manager] model call failed: {exc}", file=sys.stderr)
        return ""


# ── Part 3 — Skill creation (model-judged) ─────────────────────────────────────

# The bar the judge applies before writing anything. Spells out both what makes
# a durable skill and the failure modes that must NOT be codified — environment
# quirks, transient errors, and "tool is broken" claims that harden into
# self-imposed refusals long after the real problem is fixed.
_SKILL_JUDGE_RULES = (
    "Save a skill ONLY when ALL of these hold:\n"
    "  - The task SUCCEEDED. Never write a skill for a task that failed or was abandoned.\n"
    "  - A reusable, non-trivial technique or multi-step workflow emerged that a\n"
    "    future task of the same CLASS would benefit from.\n"
    "  - The lesson is durable — it will still be true next week.\n\n"
    "Do NOT save a skill (reply SKIP) for any of these:\n"
    "  - Simple one-off tasks any agent could do without a written procedure.\n"
    "  - Environment-specific failures: missing binaries, 'command not found',\n"
    "    unset credentials, uninstalled packages. The user fixes these; they are\n"
    "    not durable rules.\n"
    "  - Negative claims about tools ('X is broken', 'cannot use Y'). These harden\n"
    "    into self-imposed refusals long after the real issue is fixed.\n"
    "  - Transient errors that resolved before the task ended. If a retry worked,\n"
    "    the lesson is at most the retry pattern, not the original error.\n"
    "  - One-off narratives (summarize this, analyze that) — not a class of work.\n"
)


def _is_saveable_skill(content: str) -> bool:
    """True only if the model returned an actual skill, not a SKIP line or junk."""
    if not content:
        return False
    text = content.strip()
    if text.upper().startswith("SKIP"):
        return False
    has_heading   = text.startswith("##") or "\n##" in text
    has_structure = "**Applies to:**" in text and "**Steps:**" in text
    return has_heading and has_structure


async def create_skill_background(
    task_history: str,
    agent_name: str = "",
    outcome_failed: bool = False,
    had_retry: bool = False,
) -> None:
    """
    Background task: DECIDE whether a completed task is worth a skill, and write
    it only if so.

    The model is the judge. Given the transcript and the outcome it either
    declines with a 'SKIP: <reason>' line or returns the skill markdown. This
    replaces the old behaviour where any sufficiently long task produced a skill
    regardless of whether it actually succeeded.

    Non-blocking — called via asyncio.create_task().
    """
    sdir = _skills_dir()
    if not sdir:
        return

    # Gate: a task that ended in failure never produces a skill. Skip before
    # spending a model call.
    if outcome_failed:
        print("[skill] task ended in failure — no skill created", file=sys.stderr)
        return

    sdir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    retry_note = (
        "The agent self-corrected at least once before succeeding.\n\n"
        if had_retry else ""
    )

    prompt = (
        "You are reviewing a COMPLETED agent task to decide whether it is worth\n"
        "saving as a reusable skill. Judge honestly — most tasks are NOT.\n\n"
        f"{_SKILL_JUDGE_RULES}\n"
        f"{retry_note}"
        "If it is NOT worth saving, reply with exactly one line and nothing else:\n"
        "SKIP: <short reason>\n\n"
        "If it IS worth saving, reply with ONLY the skill markdown in EXACTLY this format:\n"
        "## <skill_name>\n"
        "**Applies to:** <one line: when to use this skill>\n"
        "**Steps:**\n1. ...\n2. ...\n"
        "**Notes:** ...\n"
        f"**Created:** {today}\n"
        "**Used:** 0\n"
        f"**Last improved:** {today}\n\n"
        "Be specific and actionable. Return either the SKIP line or the markdown — nothing else.\n\n"
        f"Task history:\n{task_history[-3000:]}"
    )

    content = await _model_call([
        {"role": "system", "content": "You decide whether an agent task is worth saving as a reusable skill, and write the skill only if it clears the bar."},
        {"role": "user",   "content": prompt},
    ], max_tokens=800)

    if not _is_saveable_skill(content):
        reason = (content.strip().splitlines()[0] if content.strip() else "empty response")
        print(f"[skill] nothing saved — {reason[:120]}", file=sys.stderr)
        return

    name     = _safe_name(content)
    out_path = sdir / f"{name}.md"
    if out_path.exists():
        name     = f"{name}_{int(time.time()) % 10000}"
        out_path = sdir / f"{name}.md"

    try:
        _atomic_write(out_path, content)
        record_skill_created(name, by="agent")
        print(f"[skill saved] {name}", file=sys.stderr)
    except Exception as exc:
        print(f"[skill_manager] could not save skill: {exc}", file=sys.stderr)


# ── Part 4 — Curator ──────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    wa = set(re.findall(r'\b\w{4,}\b', a.lower()))
    wb = set(re.findall(r'\b\w{4,}\b', b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ── Curator state (self-throttle) ──────────────────────────────────────────────

def _curator_state_path() -> Path | None:
    sdir = _skills_dir()
    return (sdir / ".curator_state.json") if sdir else None


def _curator_recently_ran() -> bool:
    """True if the curator ran within curator_interval_hours — avoids re-spending
    model calls on every restart."""
    p = _curator_state_path()
    if not p or not p.exists():
        return False
    try:
        last = json.loads(p.read_text(encoding="utf-8")).get("last_run_at")
        if not last:
            return False
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        return delta.total_seconds() < _curator_interval_hrs() * 3600
    except Exception:
        return False


def _mark_curator_run() -> None:
    p = _curator_state_path()
    if not p:
        return
    try:
        _atomic_write(p, json.dumps({"last_run_at": _now_iso()}, indent=2))
    except Exception:
        pass


def _backfill_provenance() -> None:
    """Anet has no user-authored skill path, so any skill file lacking a usage
    record was agent-created. Seed a record (created_at from file mtime, use_count
    from the markdown header) so the curator can manage pre-existing skills."""
    sdir = _skills_dir()
    if not sdir:
        return
    data = _load_usage()
    changed = False
    for f in sdir.glob("*.md"):
        if not f.is_file() or f.stem in data:
            continue
        _, _, used = read_skill_header(f)
        try:
            created = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            created = _now_iso()
        rec = _empty_record()
        rec.update({"created_by": "agent", "created_at": created, "use_count": used})
        data[f.stem] = rec
        changed = True
    if changed:
        _save_usage(data)


def _archive_skill_file(path: Path, archive_dir: Path) -> None:
    """Move a skill .md into archived/ (recoverable) and drop its active usage
    record. Never deletes."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / path.name
    if dest.exists():
        dest = archive_dir / f"{path.stem}_{int(time.time()) % 10000}.md"
    try:
        path.rename(dest)
    except Exception:
        return
    forget_usage(path.stem)


async def run_curator() -> None:
    """
    Maintain the agent-created skill collection. Strict invariants:
      - Only touches skills marked created_by == "agent" in the usage sidecar.
        Hand-written / unmarked-as-user skills are never modified.
      - Never deletes. Stale and merged-away skills are ARCHIVED (recoverable).
      - Pinned skills bypass every transition.
      - Self-throttled: skips if it ran within curator_interval_hours.

    Passes:
      1. Archive skills unused for > stale_after_days.
      2. Merge groups of >70%-similar skills into one.
      3. Improve skills used >= 3 times.
    """
    sdir = _skills_dir()
    if not sdir or not sdir.exists():
        return
    if _curator_recently_ran():
        return

    skill_files = [f for f in sdir.glob("*.md") if f.is_file()]
    if len(skill_files) < _curator_min_skills():
        return

    _backfill_provenance()
    _mark_curator_run()
    print(f"[curator] reviewing {len(skill_files)} skills", file=sys.stderr)
    archive = sdir / "archived"
    today   = date.today().isoformat()

    # Provenance gate — only agent-created, non-pinned, active skills are eligible.
    def _eligible(path: Path) -> bool:
        rec = get_usage_record(path.stem)
        return (
            rec.get("created_by") == "agent"
            and not rec.get("pinned")
            and rec.get("state") != "archived"
        )

    # ── Pass 1 — archive stale skills ─────────────────────────────────────────
    stale_days = _stale_after_days()
    now = datetime.now(timezone.utc)
    for f in list(skill_files):
        if not _eligible(f):
            continue
        rec = get_usage_record(f.stem)
        anchor = rec.get("last_used_at") or rec.get("created_at")
        if not anchor:
            continue
        try:
            age_days = (now - datetime.fromisoformat(anchor)).total_seconds() / 86400
        except Exception:
            continue
        if age_days > stale_days:
            _archive_skill_file(f, archive)
            print(f"[curator] archived stale skill '{f.stem}' (unused {int(age_days)}d)", file=sys.stderr)

    # ── Pass 2 — merge similar skills ─────────────────────────────────────────
    skill_files = [f for f in sdir.glob("*.md") if f.is_file() and _eligible(f)]
    skills: list[dict] = []
    for f in skill_files:
        try:
            content = f.read_text(encoding="utf-8")
            _, applies, _ = read_skill_header(f)
            skills.append({"path": f, "content": content, "applies": applies})
        except Exception:
            continue

    visited: set[int] = set()
    for i, a in enumerate(skills):
        if i in visited:
            continue
        group = [i]
        for j, b in enumerate(skills):
            if j <= i or j in visited:
                continue
            sim = _jaccard(
                a["path"].stem + " " + a["applies"],
                b["path"].stem + " " + b["applies"],
            )
            if sim > 0.7:
                group.append(j)
        if len(group) < 2:
            continue

        visited.update(group)
        combined = "\n\n---\n\n".join(skills[k]["content"] for k in group)
        merged = await _model_call([
            {"role": "system", "content": "You merge and improve skill procedure files for an AI agent."},
            {"role": "user",   "content": (
                "These skill files overlap. Merge them into one better, more specific skill.\n"
                f"Use the same markdown format. Set Created: {today}, Used: 0, "
                f"Last improved: {today}.\nReturn only the merged markdown.\n\n{combined}"
            )},
        ], max_tokens=800)

        if not merged:
            continue

        merged_name = _safe_name(merged)
        merged_path = sdir / f"{merged_name}.md"
        if merged_path.exists():
            merged_name = f"{merged_name}_{int(time.time()) % 10000}"
            merged_path = sdir / f"{merged_name}.md"
        _atomic_write(merged_path, merged)
        record_skill_created(merged_name, by="agent")
        for k in group:
            _archive_skill_file(skills[k]["path"], archive)
        print(f"[curator] merged {len(group)} skills → {merged_name}", file=sys.stderr)

    # ── Pass 3 — improve high-usage skills ────────────────────────────────────
    for f in sdir.glob("*.md"):
        if not f.is_file() or not _eligible(f):
            continue
        rec  = get_usage_record(f.stem)
        used = int(rec.get("use_count") or 0)
        if used < 3:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            improved = await _model_call([
                {"role": "system", "content": "You improve skill procedure files for an AI agent."},
                {"role": "user",   "content": (
                    f"This skill has been used {used} times. Make it more specific and actionable. "
                    "Keep the same markdown format. Return only the improved markdown.\n\n"
                    f"{content}"
                )},
            ], max_tokens=800)
            if improved:
                _atomic_write(f, improved)
                print(f"[curator] improved {f.stem} (used {used}×)", file=sys.stderr)
        except Exception as exc:
            print(f"[curator] error on {f.name}: {exc}", file=sys.stderr)
