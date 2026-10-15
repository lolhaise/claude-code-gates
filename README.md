# claude-code-gates

Hooks that stop Claude Code from shipping a reply it cannot back up: a stale fact stated as current, an invented dollar figure, an action that never ran, a claim about its own tooling from memory, AI-patterned prose.

## Numbers

From the bounce log of the machine these gates were carved out of, running Claude Code every day from 2026-08-21 to 2026-10-15:

```
cutoff tripwire          1,148 replies bounced
slop gate                  888
number gate                246
runtime-truth tripwire     221
claim gate                  15
image tripwire               6
                         -----
                         2,524   (2,491 in real sessions, 33 from test and replay runs)
```

- 238 offline test cases, one command, no model calls.
- 25 to 38 ms per hook, cold start.
- A model call only when a reply passes the free triage with no live read behind it: one `claude -p` judge call, measured at 5 s on Haiku.

## One real bounce

The reply, from a movie recommendation:

> The New York Times made it a Critic's Pick and told people not to even watch the trailer, and it's sitting at 93 on the tomato meter.

What the gate sent back before the user saw it:

> [cutoff tripwire] This reply states 'The New York Times made it a Critic's Pick and told people not to even watch the trailer, and it's sitting at 93 on the tomato meter.' as current fact and no web search, web fetch or docs read happened this turn. Where did this come from? If it is from training data, say so in the reply or verify it. Verify it with a live source and cite it, or mark it explicitly as unverified training-data knowledge, then finish.

The model gets one rewrite before the user sees anything, and the bounce says exactly what that rewrite has to do: cite a live source or say the figure is from memory.

## Install

```sh
git clone <REPO_URL> ~/.claude/gates
chmod +x ~/.claude/gates/hooks/*.py ~/.claude/gates/install.py
cp -n ~/.claude/gates/settings.example.json ~/.claude/settings.json
```

Already have a `~/.claude/settings.json`? Run `python3 ~/.claude/gates/install.py` instead of the last line. It merges the hooks block, keeps your other settings and hooks, writes a `.bak-gates` backup, and is safe to run twice.

Needs Python 3.8+ (standard library only) on Linux or macOS, and a Claude Code recent enough to have the `StopFailure`, `ConfigChange` and `PostToolUseFailure` hook events. The two judged tripwires call `claude -p` on your existing login, so `claude` must be on `PATH`; without it they stand down and every other gate still runs.

Check it: `python3 ~/.claude/gates/tests/run_tests.py` prints `238/238 passed`.

## Every gate, the same four rules

- **Fails open.** Bad JSON on stdin, an unreadable transcript, a judge timeout, a missing `claude` binary, any exception: the reply ships and the tool call runs. A guard that blocks on its own malfunction holds every reply hostage.
- **One bounce per turn.** A bounced reply comes back with `stop_hook_active` set and every gate stands down, so the worst case is one rewrite, never a loop.
- **Kill switches with no restart**, read on every call: `CLAUDE_GATES=off` for all, `CLAUDE_GATE_<NAME>=off` for one, or `touch ~/.claude/gates-state/off/<name>` (`off/ALL` for all).
- **Every bounce is logged** to `~/.claude/gates-state/bounces.jsonl`. `python3 ~/.claude/gates/hooks/_gate.py stats` turns "is it firing too much" into a count.

The six reply gates run from one Stop hook, `stop_chain.py`, in cost order: the free regex gates first, then the two judged tripwires sharing a single model call. Claude Code runs hooks for one event in parallel, so six separate Stop hooks could bounce the same reply six times; the chain makes the first gate that fires the only one.

## The gates

### Cutoff tripwire (`cutoff`, Stop)
Catches a current fact stated with no live read behind it: a price, a context window, who runs a company, what a product supports. A free topic gate looks for time-sensitive words or specific markers (money, dates, numbers with units, years, quotations, multi-word names), minus anything the user typed this turn. If the turn ran no web search, fetch, `curl` or docs read, one judge call asks where the specific claim came from: a tool result, the user, the speaker's files, or training data. Only training data with a specific claim bounces. Extra docs folders that count as a live read: `CLAUDE_GATE_CUTOFF_DOCS=path1:path2`.

### Slop gate (`slop`, Stop)
Catches prose that scores as AI-patterned and quotes each spot so the rewrite is targeted. Structure scores (the "it isn't X, it's Y" pivot, "No X. No Y. No Z." cadence, throat-clearing openers, chatbot residue, machine-even sentence lengths); vocabulary like "delve" is reported and never fails a reply on its own. The em dash is a hard block; `CLAUDE_GATE_SLOP_DASH=allow` makes it report-only. Also a CLI: `hooks/slop_gate.py --score draft.md`, and `--learn 'REGEX' --note why` to add your own pattern.

### Number gate (`number`, Stop)
Catches a dollar figure that is grounded nowhere. The amount must appear in something the user typed or a tool returned this session, or in a fact file (`./CLAUDE.md`, `~/.claude/CLAUDE.md`, or `CLAUDE_GATE_NUMBER_FACTS=path1:path2`). The assistant's own earlier lines never count, or a draft would ground itself.

### Runtime-truth tripwire (`runtime`, Stop)
Catches a claim about how the tool itself works ("hooks hot-reload", "that setting needs a restart") when the turn read no settings file, `CLAUDE.md`, `claude --help`, Claude Code docs page or hook source. A web search does not count: it says nothing about how this install is wired. Extra sources: `CLAUDE_GATE_RUNTIME_DOCS=path1:path2`.

### Claim gate (`claim`, Stop)
Catches "I sent it", "I ran the tests", "I checked the logs" in a turn where no tool ran at all. Free and deterministic: the past-tense verb has to open a clause, and negations, hypotheticals, reported speech, earlier-turn references and quoted drafts are skipped sentence by sentence.

### Image tripwire (`image`, Stop)
Catches "there are no photos of X" when no image search ran this turn. A web search about images, or any tool call carrying an image-search URL (Google Images, Bing Images, DuckDuckGo images), clears it. Your own search command: `CLAUDE_GATE_IMAGE_SEARCH_RX='my-img-search'`.

### Deny list (`deny`, PreToolUse on Bash)
Three narrow traps, each denial teaching the right form:
- bare `rm` becomes "use `trash`", because a trashed file can be restored (`git`, `npm`, `docker` and `apt` rm are exempt)
- `journalctl --since "today HH:MM"`, a form not every systemd accepts, whose failure reads as zero events
- `2>/dev/null` on a WSL `/mnt/c/` interop call, where a missing binary returns an empty result that reads as a real answer

### Failure trace (`trace`, PostToolUseFailure)
Every failed tool call appends `{ts, cwd, tool, input, error}` to `~/.claude/gates-state/tool-failures.jsonl` (or `CLAUDE_GATE_TRACE_FILE`), secrets redacted first. "What keeps failing" becomes a grep.

### Config tamper gate (`config`, ConfigChange)
Blocks a settings edit that removes a hook or points one somewhere else, so nothing can quietly unplug the gates mid-session. Added hooks and timeout changes pass and become the new baseline. For a deliberate removal, `touch ~/.claude/gates-state/config-ALLOW` (single use, 15 minutes) and repeat the edit. With the gate off it only logs, and the baseline stays frozen so the next run still sees the removal.

### StopFailure alerter (`stopfail`, StopFailure)
When a turn dies on an API error (rate limit, overload, auth, billing), Claude Code fires `StopFailure` instead of `Stop` and ignores the hook's output, so without this an eaten turn surfaces hours later. It writes a ledger row first, always, then alerts through `CLAUDE_GATES_WEBHOOK_URL` (a JSON POST with `text` and `content`, posted from a detached process so it survives teardown), `CLAUDE_GATES_NOTIFY_FILE`, or stdout. One alert per error type per 15 minutes, and the next alert says how many it held back. Turning alerts off never turns the ledger off.

### Live clock (`clock`, UserPromptSubmit)
Injects `[live-clock] Thursday 2026-10-15 19:02 UTC · session started 18:40 (22m in)` on every prompt, so "last night" and "two hours ago" get computed instead of guessed.

## Tests

```sh
python3 tests/run_tests.py
```

Offline and isolated: a temp `HOME`, a temp state folder, the judge always faked. Each gate's cases were ported from the selftests of the hooks these came from, plus process-level checks that every hook exits 0 on garbage input.

## Future

- A warm daemon. The originals run behind a resident process that keeps the hooks compiled; at 25 to 38 ms per cold start it is not needed here yet.
- Rewrite instead of deny for the simplest `rm` (return the command as `trash -- <files>` through `updatedInput`), which saves the model a retry.
- A demo recording of a bounce and its rewrite: <DEMO_LINK>

## License

MIT. Built with Claude Code, and run against its own replies every day.
