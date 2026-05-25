"""
skill_manager.py — self-improving skills system for ANet.

Skills are markdown procedure files in skills/ that agents write for
themselves after complex tasks and read before relevant new ones.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from datetime import date
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


def _max_injected()        -> int: return int(_cfg().get("max_injected",        3))
def _creation_threshold()  -> int: return int(_cfg().get("creation_threshold",  6))
def _curator_min_skills()  -> int: return int(_cfg().get("curator_min_skills",  5))


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
            path.write_text(content, encoding="utf-8")
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
        if provider in ("vertex_google", "vertex_claude"):
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


# ── Part 3 — Skill creation ────────────────────────────────────────────────────

async def create_skill_background(task_history: str, agent_name: str = "") -> None:
    """
    Background task: write a skill file from a complex task's history.
    Non-blocking — called via asyncio.create_task().
    """
    sdir = _skills_dir()
    if not sdir:
        return
    sdir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    prompt = (
        "This task was complex and involved self-corrections. "
        "Write a reusable skill procedure that would help complete similar tasks faster in future.\n\n"
        "Use EXACTLY this markdown format:\n"
        f"## <skill_name>\n"
        f"**Applies to:** <one line: when to use this skill>\n"
        f"**Steps:**\n1. ...\n2. ...\n"
        f"**Notes:** ...\n"
        f"**Created:** {today}\n"
        f"**Used:** 0\n"
        f"**Last improved:** {today}\n\n"
        "Be specific and actionable. Return only the markdown, nothing else.\n\n"
        f"Task history:\n{task_history[-3000:]}"
    )

    content = await _model_call([
        {"role": "system", "content": "You write reusable procedure files for an AI agent."},
        {"role": "user",   "content": prompt},
    ], max_tokens=800)

    if not content:
        return

    name     = _safe_name(content)
    out_path = sdir / f"{name}.md"
    if out_path.exists():
        name     = f"{name}_{int(time.time()) % 10000}"
        out_path = sdir / f"{name}.md"

    try:
        out_path.write_text(content, encoding="utf-8")
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


async def run_curator() -> None:
    """
    Review all skill files:
    - Merge skill groups with >70% similarity (archives originals, never deletes)
    - Improve skills that have been used >= 3 times
    """
    sdir = _skills_dir()
    if not sdir or not sdir.exists():
        return

    skill_files = [f for f in sdir.glob("*.md") if f.is_file()]
    if len(skill_files) < _curator_min_skills():
        return

    print(f"[curator] reviewing {len(skill_files)} skills", file=sys.stderr)
    archive = sdir / "archived"
    today   = date.today().isoformat()

    # ── Merge similar skills ──────────────────────────────────────────────────
    skills: list[dict] = []
    for f in skill_files:
        try:
            content = f.read_text(encoding="utf-8")
            _, applies, used = read_skill_header(f)
            skills.append({"path": f, "content": content, "applies": applies, "used": used})
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

        merged_path = sdir / f"{_safe_name(merged)}.md"
        merged_path.write_text(merged, encoding="utf-8")
        archive.mkdir(parents=True, exist_ok=True)
        for k in group:
            orig = skills[k]["path"]
            try:
                orig.rename(archive / orig.name)
            except Exception:
                pass
        print(f"[curator] merged {len(group)} skills → {merged_path.stem}", file=sys.stderr)

    # ── Improve high-usage skills ─────────────────────────────────────────────
    for f in sdir.glob("*.md"):
        if not f.is_file():
            continue
        try:
            content = f.read_text(encoding="utf-8")
            _, _, used = read_skill_header(f)
            if used < 3:
                continue
            improved = await _model_call([
                {"role": "system", "content": "You improve skill procedure files for an AI agent."},
                {"role": "user",   "content": (
                    f"This skill has been used {used} times. Make it more specific and actionable. "
                    "Keep the same markdown format. Return only the improved markdown.\n\n"
                    f"{content}"
                )},
            ], max_tokens=800)
            if improved:
                f.write_text(improved, encoding="utf-8")
                print(f"[curator] improved {f.stem} (used {used}×)", file=sys.stderr)
        except Exception as exc:
            print(f"[curator] error on {f.name}: {exc}", file=sys.stderr)
