# Captain's Deck

**See the whole crew. Unblock what matters. Let Firstmate ship the rest.**

Captain's Deck is a read-only **[Firstmate flow](https://github.com/kunchenguid/firstmate)** kanban plugin for [Herdr](https://herdr.dev).
It is the captain's view of agent orchestration: small surface area, like [Pi](https://pi.dev/)—one board that does one job well instead of another dashboard to babysit.

![The Captain's Deck flow board in Herdr: crew tabs, five fixed columns, and ticket cards with live status badges](assets/captains-deck.png)

![The Captain's Call decision dialog open over the board, with options, the recommended choice, a freeform note row, and Queue answer](assets/captains-call-decision-dialog.png)

Source: <https://github.com/deimantasnork/captains-deck>

## Why Captain's Deck

Parallel coding agents only feel like a crew when nothing important stalls in a forgotten tab.
[Firstmate](https://github.com/kunchenguid/firstmate) runs that crew for you: isolated worktrees per task, supervision until the work is actually finished, and a clean handoff when the session is done.

Instead of repeatedly checking agents, opening pull requests, watching CI, rebasing branches, resolving merge-flow interruptions, and rerunning validation, Firstmate keeps the delivery loop moving for you.

### Traditional workflow vs Firstmate

| Traditional AI development | Firstmate + Captain's Deck |
| --- | --- |
| Create task | Create task |
| Start agent | Agent starts in an isolated worktree |
| Check progress manually | Firstmate supervises progress |
| Check whether work finished | Completion is tracked automatically |
| Commit + push | Handled as part of the workflow |
| Open PR | PR prepared automatically |
| Watch CI / validation | Validation pipeline is monitored |
| Update / rebase branch | Ship branch stays aligned with `main` |
| Resolve merge conflicts | Firstmate works through conflicts |
| Re-run validation | Configured checks run as part of the flow |
| Review + merge | Can land automatically when policy allows |
| Repeat for every agent | Crew keeps moving in parallel |
| **You operate the workflow** | **You handle the decisions that need you** |

```text
Traditional

Task → Agent → Check → Push → PR → CI → Fix → Rebase → Test → Merge
         ↑                         ↓
         └──── human keeps coming back ────┘


Firstmate + Captain's Deck

Task → Agent → Worktree → PR → Validate → Land
           │                         │
           └────── Firstmate ────────┘
                         │
                  Human needed?
                         │
                  Captain's Call
                         │
                  Captain's Deck
```

Firstmate's project modes (`no-mistakes`, `direct-PR`, `local-only`, and optional **`+yolo`** merge autonomy) prepare the PR, keep ship branches aligned with `main`, work through conflicts, and run the configured validation pipeline while the task closes.
When policy allows `+yolo`, landing can happen without you clicking merge.

### What could that save?

The estimates below are **preliminary directional estimates, not benchmark results**.
They estimate **human orchestration overhead only**: checking agents, opening PRs, monitoring CI, updating branches, handling merge-flow interruptions, rerunning validation, and merging.
They do not include the actual coding time performed by the agent.

| Task effort | Traditional human touchpoints | With Firstmate | Potential actions removed | Estimated orchestration time saved |
| --- | ---: | ---: | ---: | ---: |
| **Small** — simple fix / small change | ~6–8 | ~1–3 | **4–6** | **~10–20 min** |
| **Medium** — normal feature / multi-file change | ~10–14 | ~2–4 | **7–10** | **~30–60 min** |
| **Large** — complex feature / migration / significant refactor | ~15–25 | ~3–6 | **10–20+** | **~1–2+ hours** |

The advantage compounds when several agents work at the same time.
For example, **five medium tasks** can create roughly **50–70 human coordination touchpoints** in a conventional workflow.
With Firstmate handling the routine delivery loop, that could fall to roughly **10–20 meaningful human interactions**.

> **Not necessarily less engineering work — dramatically less orchestration work.**

### Where Captain's Deck fits

Automation still needs a captain for real decisions.
When something is **blocked**, ambiguous, or genuinely requires human judgment, Firstmate exposes it as a **Captain's Call**.
Everything else should keep moving without interrupting your flow.

Captain's Deck is that bridge: a kanban board where you can:

- **see the whole crew at a glance**,
- spot blocking work and Captain's Calls immediately,
- answer a Captain's Call in place — choose an option, add a note, queue the answer, and let Firstmate resume the lane,
- jump to the live Herdr pane when you actually need eyes on an agent,
- and leave everything else running without babysitting terminals.

The board stays read-only except for those guarded keyed answers and the steering they trigger, so orchestration keeps running and you only touch what actually requires the captain.
Submitting an answer records the durable decision, steers the owning lane's inbox, and (on a first Reconcile) binds this Deck as the captured source; the dialog spells those effects out before you queue, and `FM_FLOW_WAKE=0` keeps a submit to the intake alone.

**You steer the ship. Firstmate runs the crew. Captain's Deck shows you where your attention is actually needed.**

## The board

One board, every crew: an **All** tab (first in the row) merges every captain
and secondmate home into one view — only work that is planned or still running
(Charted Next, Underway, Captain's Call, and Awaiting Merge). Landed rows stay
on each mate's own tab. After **All**, the captain home plus each secondmate
home appear as crew tabs. Each tab projects that home's bearings snapshot into
five fixed columns. Nothing is ever written back, with one deliberate exception: a
Captain's Call answer (see below), where the captain's own decision goes to
Firstmate's guarded keyed-answer intake, and the owning lane is steered so the
answer is acted on.

The fleet board folds a task that more than one home lists - a captain home
mirrors delegated work as `<mate>/<task>` while the mate's own home keeps
`<task>` - into the owning home's row, in the column that row reports, so one
task is one card. Captain's Call rows already fold that way for answer routing;
two same-named tasks in different repos (no matching namespace, run, or
worktree) stay separate.

| Column | Source |
| --- | --- |
| **Charted Next** | `gates` |
| **Underway** | every `in_flight` row (badges carry `shipping` / `validating` / `parked` / `paused` / `failed`) |
| **Captain's Call** | `decisions_open` - click a ticket to decide it in place |
| **Awaiting Merge** | `in_flight` rows whose Firstmate `state` is `done` (crew finished, waiting on merge/review) |
| **Landed** | `landed` — Firstmate's "Recently Landed": merged PRs, completed scouts, local-only merges (hidden by default here; toggle with `L`) |

Firstmate's own bearings has four sections (Underway, Charted Next, Captain's Call,
Recently Landed) and deliberately keeps run status out of the section split.
Awaiting Merge is the only added projection: Firstmate's own `state == "done"`
rows, which are the ones waiting on a merge.

The board uses **Firstmate's own bounds** by default (`FM_BEARINGS_LANDED` = 6
newest per home, gates/in-flight = 20). `FM_FLOW_ALL=1` requests every row.

Crew tabs show a live activity dot (`●` working/blocked, `○` agent present) and
the number of tickets on that board once it has been visited; a visited board
with no tickets shows `(0)`. **All** shows the fleet-wide planned/running count
(captain plus every secondmate); mate tabs count every column including Landed
when that column is visible.

## What each ticket shows

```text
╭─ demo-issue-197 ─────────────────────╮
│ ◐ validating                         │   live badge
│ claude·opus·xhigh                    │   harness · model · thinking effort
│ Add retry to the…                    │   title / summary / landed what
│ 9m 59s · ↓ 55.5k tokens · xhigh      │   total run time · tokens · thinking
│ ⌸ 4/my-app-feat…                     │   worktree (or ↗ PR artifact for landed)
╰──────────────────────────────────────╯
```

Badges: `● shipping`, `◐ validating`, `⛔ blocked`, `⚑ decision` /
`⚑ captain`, `◍ awaits merge`, `⏸ parked` / `⏸ paused`, `⛔ failed`,
`✓ done` / `✓ landed`, `· queued`.
Live state comes from `herdr agent list`; activity and review state come from
Firstmate's bearings snapshot and the home's `state/<task>.status` tail.
While an agent is **shipping**, **validating**, or **blocked**, the
**doing/status** line carries total run time and total token use in the same
style as Herdr's agent sidebar (wall time since task spawn when available;
tokens from detection or cumulative Pi session usage). A `doing` value that
only repeats the badge (`validating`, `validating: …`) or the generic
`harness busy …` line is dropped, so the row shows the totals alone; a `doing`
that adds real detail keeps it before the totals. Validating and blocked rows
also carry the thinking effort when the row has room
(`9m 59s · ↓ 55.5k tokens · xhigh`).

## Controls

The footer shows the frequent actions - `? help`, `L - Show/Hide Landed`, and
`r - Refresh board` - and each one is clickable. The help modal (click
`? help` or press `?`) lists every binding: `←→` / `h l` move between columns,
`↑↓` / `j k` move between cards, `pgup`/`pgdn` or the wheel scroll a column,
clicking a crew tab switches mate, `enter` or a click opens a Captain's Call
ticket, `o` opens the selected agent pane, `1-9` / `tab` switch crew
(All = fleet), `L` shows or hides the Landed column, `r` refreshes the board,
and `q` quits. `esc` or `?` closes the modal, and a click outside it closes it
too.

## Keeping up with Firstmate updates

Firstmate updates itself (`updatefirstmate`, fleet sync) and this deck reads
its live surfaces from the home, so drift usually shows up as empty or quiet
cards rather than an error. Three guards keep that visible:

- **Contract check** - `scripts/fm-contract-check.sh --home ~/firstmate` (or
  `python3 scripts/fm_contract_check.py --home ~/firstmate --json`) runs the
  home's `bin/fm-bearings-snapshot.sh --json --fields paths` and asserts every
  section, item field, meta key, status line, decision card, and the
  `fm-captain-hold.sh` / `fm-send.sh` intakes that the deck reads. Exit code 1
  lists the missing surface; run it after an `updatefirstmate`.
- **Recorded fixtures** - `tests/fixtures/` keeps a sanitized snapshot and a
  minimal home. `tests/test_fixture_board.py` proves the recorded shape still
  projects into the same columns, and `tests/test_fm_contract_check.py` proves
  the checker itself catches a dropped field.
- **Nightly drift alarm** - `.github/workflows/firstmate-contract.yml` clones
  `kunchenguid/firstmate@main` every night, runs the contract check against it,
  and opens or comments on an issue labelled `firstmate-drift` when a surface
  changes.

For field reports, `FM_FLOW_DEBUG=/tmp/fm_flow_debug.log` stamps the deck
revision and each home's Firstmate revision on every collector pass, so a log
pins the exact pair.

## Answering a Captain's Call ticket

Clicking a Captain's Call ticket (or pressing `Enter` on it) opens its decision
card as a modal, composed from the same `fm-bearings-board.v1` card the Lavish
bearings board renders: the type badge and repo, the title, the `ABOUT` /
`DECIDE` context, every authored option with its hint, the `REC` mark on the
recommended one, a freeform note row, and **Queue answer**.

- `↑`/`↓` (or `j`/`k`) move, `space` picks or clears an option, typing edits
the note, `Tab` jumps between the options and the note, `Enter` queues, and
`esc` closes without answering.
- The answer is piped to Firstmate's one keyed-answer intake
(`bin/fm-captain-hold.sh answers`) in the home that owns the ticket, with this
Deck as its provenance. The key, answer and label are flattened first, so a
newline or tab in card text can never become a second answer row. A card that
declares `close: "release"` releases the gated work instead of completing it.
- The dialog shows the option's value whenever it differs from its label, names
the card-declared close on the submit button (`Queue answer · releases hold`),
and prints one line of what a submit does beyond the record: the exact key, the
close or release, and the lane that will be steered.
- After the record lands, the Deck steers the agent that owns the call through
the parent home's lane inbox (`fm-send.sh`), so a released item resumes and a
re-check actually gets worked. A wake problem is shown beside the queued state;
the recorded answer is never reversed. Set `FM_FLOW_WAKE=0` (or `wake_owner=0`
in the config dir) to keep a submit to the intake alone.
- `Reconcile` is the reserved value: it files a durable reconcile request
through `reconcile-requests`, binding `herdr-firstmate-flow` as its captured
source on first use, and never closes anything by itself. The call leaves
Captain's Call immediately - the snapshot buckets it `reconciling` and shows it
under Charted Next as `reconcile requested <time>` - and returns only if the
owner finds it still active.
- Card content is read from the live board, then the durable store the board
build writes (`state/decision-cards/<task>.json`), then the composed payload
history. That store is what keeps the authored options available after a board
rebuild.
- When no composed card exists for a ticket, the dialog still opens with its
durable title, its hold reason as the `ABOUT` line, a freeform answer, and
`Reconcile`.
- Nothing is resolved by the board itself: every guard, the durable decision,
and the close all live in Firstmate.

## Freshness: only what changed is refreshed

The board never redraws itself wholesale.

- **Live tick (2s):** the active board rebuilds its badges from cheap sources —
  Herdr's agent/pane list, each task's `state/<id>.meta` and the tail of
  `state/<id>.status` (both cached by mtime).
- **Bearings (20s):** the expensive `fm-bearings-snapshot.sh` run only happens
  when the cached snapshot is older than `FM_FLOW_BEARINGS_SECS`, when you switch
  to a crew that has no cached data, or when you press `r`.
- **Diff repaint:** only the screen lines that actually changed are written, so a
  badge updating does not disturb your scroll position, selection, or the rest of
  the board.
- **Only the active crew** is refreshed; other crews cost nothing while you look
  at one board.

## Interaction

| Action | What happens |
| --- | --- |
| Click a **crew name** (top row) | Switch instantly; **All** loads every mate's bearings; other tabs use cached cards first, then refresh |
| Click a **ticket** | Focus the ticket's Herdr tab/pane, which selects that agent in the Herdr agents sidebar |
| Click a Captain's Call **ticket** | Open its decision card modal and queue the captain's answer |
| Click a ticket with no live pane | Footer explains it, e.g. `demo-issue-198: no live pane · (no worktree yet)` |
| `1`…`9`, `Tab` / `Shift+Tab`, `[` / `]` | Switch crew |
| `←`/`→` or `h`/`l` | Move between columns |
| `↑`/`↓` or `j`/`k` | Move between tickets (the view follows the selection) |
| Mouse **wheel over a column** | Scroll that column smoothly (3 rows per notch) |
| `Shift`+wheel | Scroll every column together |
| `PgUp`/`PgDn`, `g`/`G` | Page / jump within the selected column |
| `Enter` | Decide the selected Captain's Call ticket; any other ticket opens its agent pane |
| `o` | Open the selected ticket's agent pane, including a Captain's Call one |
| `r` / `q` | Force a bearings refresh / quit |
| `L` | Show/hide the Landed column |

Input repaints immediately (no waiting for the next data tick), and a burst of
wheel events is coalesced into a single repaint. Scrolling moves by terminal
rows, so cards clip at the edges instead of jumping a whole 8-row card.

Ticket → pane mapping comes from the task's `state/<task>.meta`
(`herdr_pane_id`, `herdr_tab_id`, `herdr_workspace_id`), with a fallback to
matching the worktree path against live pane working directories. The `herdr`
binary is resolved from `HERDR_BIN_PATH`, then `PATH`, then `~/.local/bin/herdr`
(plugin panes inherit a minimal `PATH`).

## Theme

Ticket borders, badges, and column titles use the terminal's **ANSI palette**
(basic 16 colours, no hardcoded RGB or 256-colour indexes), so they follow
whatever palette the Herdr `[theme]` setting gives the panes.

## Requirements

- [Herdr](https://herdr.dev/) >= 0.9.0
- [Firstmate](https://github.com/kunchenguid/firstmate) homes with `bin/fm-bearings-snapshot.sh`
- `python3` (standard library only), no `jq` required

## Home discovery

Homes are discovered in this order (first match wins per path):

1. `FM_FLOW_HOMES` environment variable — `label=path label=path`
2. `homes.conf` in the plugin config directory — one `label=path` per line
3. `FM_HOME` or the legacy `fm_home` config file
4. `~/firstmate` plus `~/.treehouse/*/*/firstmate` worktree homes

The explicit list is additive by default: setting `FM_FLOW_HOMES` or
`homes.conf` labels the homes it names, while the scan still runs. Set
`FM_FLOW_HOMES_ONLY=1` (or `homes_only=1` in the config dir) to make the
explicit list the only source; with nothing configured it falls back to the
scan rather than showing an empty board.

A home is shown when it has task directories **or** a live Herdr agent running
in it. An unleased spare treehouse worktree - no lease holder, no presentation
label, no live agent - is hidden, because it is a slot rather than a crew. Labels come from the treehouse lease holder
(`~/.treehouse/*/treehouse-state.json`) or a task's
`state/*.herdr-presentation` `parent_label`, so secondmates appear as e.g.
`2ndmate-demo`.

Plugin config lives in:

```text
~/.config/herdr/plugins/config/herdr-firstmate-flow/
  fm_home        # legacy single-home config (still honored)
  homes.conf     # optional label=path list
  show_landed    # optional: 0 hides the Landed column
  wake_owner     # optional: 0 skips the owner steer after an answer
  homes_only     # optional: 1 limits discovery to FM_FLOW_HOMES/homes.conf
  debug_log      # optional: a path, or an empty file for <config>/debug_log.log
  flow-overlay.panes  # internal: recorded overlay pane ids
  deck-flow.panes     # internal: recorded captain's deck pane ids
```

Environment knobs:

| Variable | Default | Meaning |
| --- | --- | --- |
| `FM_FLOW_TICK_SECS` | `2` | Live badge/agent refresh |
| `FM_FLOW_BEARINGS_SECS` | `20` | Bearings snapshot TTL |
| `FM_FLOW_WHEEL_ROWS` | `3` | Rows moved per wheel notch |
| `FM_FLOW_LANDED_LIMIT` | `10` | Safety cap on landed cards (Firstmate's own bound is 6/home) |
| `FM_FLOW_SHOW_LANDED` | `1` | Show the Landed column (`0` = hide it, also toggleable with `L`) |
| `FM_FLOW_ALL` | `0` | `1` = request every row from bearings instead of Firstmate's bounds |
| `FM_FLOW_HOMES` | — | Explicit `label=path` crew list |
| `FM_FLOW_HOMES_ONLY` | `0` | `1` = discover only `FM_FLOW_HOMES`/`homes.conf` homes |
| `FM_FLOW_WAKE` | `1` | `0` = record the answer without steering the owning lane |
| `FM_FLOW_DEBUG` | — | Append click/collector debug lines to this file |

## Open the board

- Action **Open flow board** — floating overlay pane
- Action **Open captain's deck** — full board in a dedicated `captain's deck`
  workspace (created on first use, refocused afterwards)
- Or bind keys in `~/.config/herdr/config.toml`

Both launchers only focus or close panes this plugin opened - tracked by
recorded pane id, or verified by the pane's foreground process being the board.
A user pane that merely shares the `Flow` label is never touched.

Press `q` in the pane to exit.

## Install / share

```bash
herdr plugin install deimantasnork/captains-deck
herdr plugin enable herdr-firstmate-flow
```

Local development:

```bash
herdr plugin link /path/to/herdr-firstmate-flow
```

Probe without a TTY (useful for CI or troubleshooting):

```bash
scripts/kanban-view.sh --homes        # list discovered homes
scripts/kanban-view.sh --once         # one plain-text frame, all homes
scripts/kanban-view.sh --once --home 2ndmate-demo
```

Herdr's plugin marketplace indexes public GitHub repositories tagged with the
`herdr-plugin` topic, and this repository is listed there:
<https://herdr.dev/plugins/>. Installation is still `herdr plugin install`.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, conventions, and how to run the checks.

Report bugs, ask questions, or suggest features with a [new GitHub issue](https://github.com/deimantasnork/captains-deck/issues/new) (templates optional; blank issues are allowed).

## Related projects

- [Herdr](https://herdr.dev/) — terminal workspaces, panes, and the agent host
  this plugin extends.
- [Herdr GPUI](https://github.com/penso/herdr-gpui) — native Rust/GPUI Herdr
  client used for the screenshots above.
- [Firstmate](https://github.com/kunchenguid/firstmate) — agent distro that runs
  the crew, closes worktrees into PRs (and optional yolo release), and owns the
  bearings snapshot and Captain's Call intake this board projects.
- [Pi](https://pi.dev/) — one of the supported primary harnesses; each ticket
  shows whichever harness, model, and thinking effort that home actually uses.
