# Contributing

Thanks for wanting to help improve Captain's Deck.

Bug reports, feature ideas, and pull requests are welcome.
You do not need maintainer access to participate.

## Report a problem or suggest a change

Open a [new issue](https://github.com/Enk1do/captains-deck/issues/new) on GitHub.
Use the templates when they fit, or choose **Open a blank issue** if none match.

Please include:

- Herdr and plugin versions (`herdr --version`, `herdr plugin list`)
- Whether you use captain-only or captain + secondmate homes
- Steps to reproduce (for bugs) or the workflow you want (for features)
- Screenshots or terminal output when they clarify the report

## Pull request workflow

1. Fork [Enk1do/captains-deck](https://github.com/Enk1do/captains-deck) on GitHub.
2. Clone your fork (or add it as a remote) and create a branch for your change.
3. Install the plugin locally for manual testing:

   ```sh
   herdr plugin link /path/to/your/captains-deck-clone
   herdr plugin enable herdr-firstmate-flow
   ```

4. Run the focused Python checks before you push:

   ```sh
   python3 tests/test_flow_decision_dialog.py
   python3 -m pytest tests/test_flow_all_crew.py -q
   ```

   `pytest` is only required for `test_flow_all_crew.py`; install it with `pip install pytest` if needed.

5. Open a pull request against `main` with a short summary and test notes.

Maintainers review PRs as time allows.
Smaller, focused changes are easier to land than large rewrites.

## Repo conventions

- `scripts/flow_tui.py` owns the Captain's Deck TUI; keep bash helpers in `scripts/`.
- `herdr-plugin.toml` is the Herdr marketplace manifest; bump `version` when you ship user-visible behavior.
- In Markdown, put each full sentence on its own line (same style as [Firstmate](https://github.com/kunchenguid/firstmate)).
- `README.md` stays a concise overview; route long detail to comments in code or issue discussion unless it belongs in the README.

## Questions

Open a [GitHub issue](https://github.com/Enk1do/captains-deck/issues/new) with the **Question** template, or a blank issue if you prefer.
