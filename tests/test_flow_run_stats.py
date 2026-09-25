"""Agent run stats parsing for Captain's Deck cards."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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


def test_card_run_stats_line():
    card = flow.Card()
    card.run_elapsed = "4m 2s"
    card.run_tokens = "12.1k"
    line = flow.UI.card_run_stats_line(card)
    assert "4m 2s" in line
    assert "↓ 12.1k" in line
