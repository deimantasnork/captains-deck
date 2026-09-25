"""Agent run stats parsing for Captain's Deck cards."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()


def test_parse_herdr_claude_status_line():
    text = "· Herding… (9m 59s · ↓ 55.5k tokens)\n"
    elapsed, tokens = flow.parse_run_stats_from_detection(text)
    assert elapsed == "9m 59s"
    assert tokens == "55.5k"


def test_parse_inline_stats():
    text = "footer (13m 30s · ↓ 75.9k tokens) more"
    elapsed, tokens = flow.parse_run_stats_from_detection(text)
    assert elapsed == "13m 30s"
    assert tokens == "75.9k"


def test_parse_pi_footer_tokens():
    text = "↑2.3M ↓31k R2.0M CH42.9%"
    elapsed, tokens = flow.parse_run_stats_from_detection(text)
    assert elapsed == ""
    assert tokens == "31k"


def test_card_run_stats_suffix():
    card = flow.Card()
    card.run_elapsed = "4m 2s"
    card.run_tokens = "12.1k"
    suffix = flow.UI.card_run_stats_suffix(card)
    assert suffix == " (4m 2s · ↓ 12.1k tok)"


def test_card_status_line_appends_stats_to_doing():
    card = flow.Card()
    card.badge = "● shipping"
    card.doing = "editing auth/client.rs"
    card.run_elapsed = "9m 59s"
    card.run_tokens = "55.5k"
    line = flow.UI.card_status_line(card, 80)
    assert line == "editing auth/client.rs (9m 59s · ↓ 55.5k tok)"


def test_card_status_line_drops_state_that_repeats_the_badge():
    card = flow.Card()
    card.badge = "◐ validating"
    card.doing = "validating: e2e payments"
    card.run_elapsed = "3m 45s"
    card.run_tokens = "75"
    line = flow.UI.card_status_line(card, 80)
    assert line == "(3m 45s · ↓ 75 tok)"


def test_card_status_line_drops_harness_busy_noise():
    card = flow.Card()
    card.badge = "● shipping"
    card.doing = "harness busy (claude-hook)"
    card.run_elapsed = "9m 59s"
    card.run_tokens = "55.5k"
    line = flow.UI.card_status_line(card, 80)
    assert line == "(9m 59s · ↓ 55.5k tok)"


def test_card_status_line_empty_without_status_or_stats():
    card = flow.Card()
    card.badge = "· queued"
    card.doing = ""
    card.status_text = ""
    assert flow.UI.card_status_line(card, 80) == ""


def test_parse_spawn_gen_epoch():
    assert flow.parse_spawn_gen_epoch("s1790288967.25969.11281") == 1790288967.0
    assert flow.parse_spawn_gen_epoch("") == 0.0


def test_pi_session_run_stats_sums_tokens():
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write(
            '{"timestamp":"2026-09-25T10:00:00+00:00","message":{"role":"assistant","usage":{"input":100,"output":50}}}\n'
            '{"timestamp":"2026-09-25T10:05:00+00:00","message":{"role":"assistant","usage":{"input":200,"output":80}}}\n'
        )
        path = fh.name
    try:
        elapsed, tokens = flow.pi_session_run_stats(path)
        assert tokens == "430"
        assert elapsed.endswith("s") or "m" in elapsed
    finally:
        Path(path).unlink(missing_ok=True)
