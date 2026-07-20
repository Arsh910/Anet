"""Unit tests for anet.QweenBee.skills (Phase 4: the skill bank). Pure, offline
— synthetic evidence rows written to tmp JSONL files, no LLM calls."""
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.QweenBee import skills

_MOTIFS = {"steps": 2, "messages": 3, "max_fan_in": 2, "fan_in_bucket": "pair",
          "has_sink": True, "num_sinks": 1, "has_audit_edge": False, "reduction_depth": 2}


def _row(run_id, request_class, n_nodes, graph_hash, *, source="planner",
        failures=0, consistency=1.0, tokens=0, spec="__default__", ts="t0"):
    if spec == "__default__":
        spec = None if source == "fallback" else {"T": 1, "rounds": [], "holder": "s1"}
    return {
        "ts": ts, "run_id": run_id, "source": source, "graph_hash": graph_hash,
        "T": 2, "n_nodes": n_nodes, "n_edges": 3, "holder": "s3",
        "motifs": dict(_MOTIFS), "request_class": request_class, "spec": spec,
        "outcome": {"failures": failures, "consistency": consistency, "tokens": tokens},
    }


def _write(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _tmp_path(name="evidence.jsonl"):
    return Path(tempfile.mkdtemp()) / name


# ── 1. loss() ────────────────────────────────────────────────────────────────

def test_loss_zero_when_perfect():
    rec = {"n_nodes": 3, "outcome": {"failures": 0, "consistency": 1.0, "tokens": 0}}
    assert skills.loss(rec) == 0.0


def test_loss_components_add_up():
    rec = {"n_nodes": 2, "outcome": {"failures": 1, "consistency": 0.5, "tokens": 10000}}
    # fail_rate=0.5*1.0=0.5, inconsistency=0.5*0.5=0.25, cost=1.0*0.05=0.05 -> 0.8
    assert abs(skills.loss(rec) - 0.8) < 1e-9


# ── 2. distill: Preserve scaffold from repeated evidence ───────────────────

def test_distill_preserve_scaffold():
    p = _tmp_path()
    rows = [_row(f"r{i}", "coding", 3, "hashA", consistency=1.0,
                spec={"idx": i}, ts=f"t{i}") for i in range(4)]
    _write(rows, p)
    skl = skills.distill(evidence_path=p)
    preserves = [s for s in skl if s["kind"] == "scaffold"]
    assert len(preserves) == 1
    sk = preserves[0]
    assert sk["stats"]["n"] == 4
    assert sk["condition"] == {"request_class": "coding", "size_bucket": "small"}
    assert sk["graph_spec"] == {"idx": 3}   # highest-ts row wins


# ── 3. Conservative credit ───────────────────────────────────────────────────

def test_low_n_group_excluded_from_champion():
    # doc's example: n=1 (mean 0.05) can't even reach N_MIN, so a worse-mean
    # n=6 group becomes champion regardless of its higher raw mean_loss.
    p = _tmp_path()
    rows = [_row("a0", "coding", 3, "hashA", consistency=0.9, ts="a0")]
    rows += [_row(f"b{i}", "coding", 3, "hashB", consistency=0.6, ts=f"b{i}") for i in range(6)]
    rows += [_row(f"c{i}", "coding", 3, "hashC", consistency=0.1, ts=f"c{i}") for i in range(3)]
    _write(rows, p)
    skl = skills.distill(evidence_path=p)
    avoids = {s["graph_hash"] for s in skl if s["kind"] == "avoid_shape"}
    assert "hashA" not in avoids     # never eligible as champion OR as a compared group
    assert "hashC" in avoids         # much worse than champion B


def test_preserve_pick_uses_risk_loss_not_raw_mean():
    # hashX: n=3, better raw mean_loss but higher risk_loss (fewer samples).
    # hashY: n=8, worse raw mean_loss but lower risk_loss (more samples).
    p = _tmp_path()
    rows = [_row(f"x{i}", "coding", 3, "hashX", consistency=1.0, ts=f"x{i}") for i in range(3)]
    rows += [_row(f"y{i}", "coding", 3, "hashY", consistency=0.95, ts=f"y{i}") for i in range(8)]
    _write(rows, p)
    mean_x, mean_y = 0.0, 0.5 * (1 - 0.95)
    assert mean_x < mean_y
    risk_x = mean_x + skills.KAPPA / math.sqrt(3)
    risk_y = mean_y + skills.KAPPA / math.sqrt(8)
    assert risk_y < risk_x
    skl = skills.distill(evidence_path=p)
    preserves = [s for s in skl if s["kind"] == "scaffold"]
    assert len(preserves) == 1 and preserves[0]["graph_hash"] == "hashY"


# ── 4. N_MIN gate ─────────────────────────────────────────────────────────────

def test_n_min_gate_blocks_sparse_evidence():
    p = _tmp_path()
    rows = [_row(f"r{i}", "coding", 3, "hashA", consistency=1.0, ts=f"t{i}") for i in range(2)]
    _write(rows, p)
    assert skills.distill(evidence_path=p) == []


# ── 5. Avoid discipline ──────────────────────────────────────────────────────

def test_avoid_requires_margin_over_champion():
    p = _tmp_path()
    rows = [_row(f"champ{i}", "coding", 3, "hashChamp", consistency=1.0, ts=f"c{i}") for i in range(3)]
    rows += [_row(f"bad{i}", "coding", 3, "hashBad", consistency=0.5, ts=f"b{i}") for i in range(3)]
    rows += [_row(f"ok{i}", "coding", 3, "hashOk", consistency=0.75, ts=f"o{i}") for i in range(3)]
    _write(rows, p)
    skl = skills.distill(evidence_path=p)
    avoids = {s["graph_hash"] for s in skl if s["kind"] == "avoid_shape"}
    assert avoids == {"hashBad"}


def test_avoid_needs_well_sampled_champion():
    p = _tmp_path()
    rows = [_row(f"r{i}", "coding", 3, "hashOnly", consistency=0.1, ts=f"t{i}") for i in range(5)]
    _write(rows, p)
    skl = skills.distill(evidence_path=p)
    assert [s for s in skl if s["kind"] == "avoid_shape"] == []


# ── 6. Fallback rows excluded from Preserve, visible in motif table ────────

def test_fallback_rows_excluded_from_preserve_but_seen_in_motifs():
    p = _tmp_path()
    rows = [_row(f"f{i}", "coding", 3, "hashFallback", source="fallback",
                consistency=1.0, ts=f"f{i}") for i in range(4)]
    _write(rows, p)
    skl = skills.distill(evidence_path=p)
    assert not any(s["kind"] == "scaffold" for s in skl)
    table = skills.motif_table(evidence_path=p)
    assert table["steps=2"]["n"] == 4


# ── 7. Structural dedup: same graph_hash merges into one group ─────────────

def test_rows_with_same_graph_hash_merge_into_one_group():
    p = _tmp_path()
    rows = [_row(f"r{i}", "coding", 3, "sameHash", consistency=1.0, ts=f"t{i}") for i in range(3)]
    _write(rows, p)
    skl = skills.distill(evidence_path=p)
    preserves = [s for s in skl if s["kind"] == "scaffold"]
    assert len(preserves) == 1
    assert preserves[0]["stats"]["n"] == 3   # merged, not three separate n=1 groups


# ── 8. motif_table ────────────────────────────────────────────────────────────

def test_motif_table_per_feature_counts():
    p = _tmp_path()
    rows = [_row(f"r{i}", "coding", 3, f"hash{i}", consistency=1.0, ts=f"t{i}") for i in range(2)]
    _write(rows, p)
    table = skills.motif_table(evidence_path=p)
    assert table["steps=2"]["n"] == 2
    assert table["fan_in_bucket=pair"]["n"] == 2
    assert "mean_loss" in table["steps=2"] and "risk_loss" in table["steps=2"]


# ── 9. retrieve: exact match, adjacent-bucket downgrade, avoid crosses buckets ─

def test_retrieve_exact_modify_and_avoid():
    p = _tmp_path()
    bankp = _tmp_path("bank.json")
    rows = [_row(f"s{i}", "coding", 3, "hashSmallGood", consistency=1.0, ts=f"s{i}",
                spec={"T": 1, "rounds": [], "holder": "s1"}) for i in range(3)]
    rows += [_row(f"b{i}", "coding", 3, "hashSmallBad", consistency=0.0, ts=f"b{i}") for i in range(3)]
    _write(rows, p)

    ctx_exact = skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert "PRESERVE" in ctx_exact and "AVOID" in ctx_exact
    assert '"T": 1' in ctx_exact

    ctx_adjacent = skills.retrieve("coding", 5, bank_path=bankp, evidence_path=p)   # medium bucket
    assert "MODIFY" in ctx_adjacent
    assert "AVOID" in ctx_adjacent   # avoid transfers across size buckets

    ctx_foreign = skills.retrieve("reasoning", 3, bank_path=bankp, evidence_path=p)
    assert ctx_foreign == ""


# ── 10. Staleness / self-refresh ────────────────────────────────────────────

def test_retrieve_refreshes_when_evidence_grows():
    p = _tmp_path()
    bankp = _tmp_path("bank.json")
    rows = [_row(f"a{i}", "coding", 3, "hashA", consistency=1.0, ts=f"a{i}") for i in range(3)]
    _write(rows, p)
    skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert skills.load_bank(bankp)["evidence_rows"] == 3

    more = [_row(f"c{i}", "coding", 3, "hashC", consistency=1.0, ts=f"c{i}") for i in range(3)]
    with open(p, "a", encoding="utf-8") as f:
        for r in more:
            f.write(json.dumps(r) + "\n")

    skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert skills.load_bank(bankp)["evidence_rows"] == 6


# ── 11. Corruption tolerance ─────────────────────────────────────────────────

def test_corrupt_bank_and_evidence_are_tolerated():
    p = _tmp_path()
    bankp = _tmp_path("bank.json")
    bankp.write_text("{not valid json", encoding="utf-8")
    p.write_text("not json\n{\"broken\n", encoding="utf-8")

    ctx = skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert ctx == ""
    assert skills.load_bank(bankp)["skills"] == []


# ── 12. Render cap ────────────────────────────────────────────────────────────

def test_render_cap_at_max_skills_in_prompt():
    p = _tmp_path()
    bankp = _tmp_path("bank.json")
    rows = [_row(f"sc{i}", "coding", 3, "smallChamp", consistency=1.0, ts=f"sc{i}") for i in range(3)]
    for j in range(3):
        rows += [_row(f"sb{j}_{i}", "coding", 3, f"smallBad{j}", consistency=0.0, ts=f"sb{j}{i}")
                for i in range(3)]
    rows += [_row(f"mc{i}", "coding", 5, "medChamp", consistency=1.0, ts=f"mc{i}") for i in range(3)]
    for j in range(2):
        rows += [_row(f"mb{j}_{i}", "coding", 5, f"medBad{j}", consistency=0.0, ts=f"mb{j}{i}")
                for i in range(3)]
    _write(rows, p)

    ctx = skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    lines = [l for l in ctx.split("\n") if l.strip()]
    assert len(lines) == skills.MAX_SKILLS_IN_PROMPT


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: qweenbee_skills")
