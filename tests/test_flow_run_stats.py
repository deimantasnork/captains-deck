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


def test_format_elapsed_deck():
    assert flow._format_elapsed_deck("3m 45s") == "3min 45s"
    assert flow._format_elapsed_deck("45s") == "45s"


def test_format_tokens_deck():
    assert flow._format_tokens_deck("75") == "75 tok"
    assert flow._format_tokens_deck("75k") == "75k tok"
    assert flow._format_tokens_deck("4.5M") == "4.5M tok"


def test_card_run_stats_suffix():
    card = flow.Card()
    card.run_elapsed = "4m 2s"
    card.run_tokens = "12.1k"
    suffix = flow.UI.card_run_stats_suffix(card)
    assert suffix == "4min 2s \u25cf 12.1k tok"


def test_card_badge_line_is_status_only():
    card = flow.Card()
    card.badge = "\u25d0 validating"
    card.live_status = "working"
    card.run_elapsed = "9m 59s"
    card.run_tokens = "55.5k"
    assert flow.UI.card_badge_line(card, 80) == "\u25d0 validating"
    assert flow.UI.card_run_line(card, 80) == "9min 59s \u25cf 55.5k tok"


def test_card_badge_line_without_stats_uses_symbol_badge():
    card = flow.Card()
    card.badge = "\u00b7 queued"
    card.live_status = "idle"
    line = flow.UI.card_badge_line(card, 80)
    assert line == "\u00b7 queued"


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


def test_card_run_line_keeps_both_totals_at_deck_width():
    card = flow.Card()
    card.live_status = "working"
    card.run_elapsed = "9m 59s"
    card.run_tokens = "55.5k"
    assert flow.UI.card_run_line(card, 24) == "9min 59s \u25cf 55.5k tok"


def test_card_run_line_degrades_on_narrow_cards():
    card = flow.Card()
    card.live_status = "blocked"
    card.run_elapsed = "1h 12m 5s"
    card.run_tokens = "1.2M"
    assert flow.UI.card_run_line(card, 24) == "1h 12min 5s \u25cf 1.2M tok"
    assert flow.UI.card_run_line(card, 18) == "1h12m5s \u25cf 1.2M tok"
    assert flow.UI.card_run_line(card, 15) == "1h12m5s \u25cf 1.2M"


def test_card_run_line_hidden_when_agent_not_live():
    card = flow.Card()
    card.live_status = "idle"
    card.run_elapsed = "9m 59s"
    card.run_tokens = "55.5k"
    assert flow.UI.card_run_line(card, 24) == ""


def test_card_why_line_skips_badge_repeats_and_harness_noise():
    card = flow.Card()
    card.bucket = "underway"
    card.badge = "\u25d0 validating"
    card.blocked_by = ""
    card.doing = "validating"
    assert flow.UI.card_why_line(card) == ""
    card.doing = "harness busy (claude-hook)"
    assert flow.UI.card_why_line(card) == ""
    card.doing = "validating: e2e payments"
    assert flow.UI.card_why_line(card) == "validating: e2e payments"


def test_card_why_line_prefers_reason_then_blocked_by():
    card = flow.Card()
    card.bucket = "charted"
    card.badge = "\u00b7 queued"
    card.doing = "waiting for captain slot"
    assert flow.UI.card_why_line(card) == "waiting for captain slot"
    card.doing = ""
    card.blocked_by = "schema-migrate-3"
    assert flow.UI.card_why_line(card) == "blocked by schema-migrate-3"
    card.bucket = "landed"
    assert flow.UI.card_why_line(card) == ""


def test_card_meta_parts_splits_agent_and_jump():
    card = flow.Card()
    card.bucket = "underway"
    card.agent = "claude"
    card.model = "opus"
    card.effort = "xhigh"
    card.worktree = "/home/x/.treehouse/my-app/4/demo-issue-197"
    assert flow.UI.card_meta_parts(card, "") == ("claude\u00b7opus\u00b7xhigh", "\u2338 4/demo-issue-197")


def test_card_meta_parts_drops_placeholder_agent_when_jump_exists():
    card = flow.Card()
    card.bucket = "charted"
    card.worktree = ""
    card.agent = card.model = card.effort = ""
    assert flow.UI.card_meta_parts(card, "") == ("", "no worktree yet")
