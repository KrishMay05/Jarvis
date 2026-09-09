# Jarvis

A personal assistant you can run locally. Drop in **one AI API key** (Gemini, OpenAI, or Anthropic) and the built-in tools work — no weather key, no search key, no extra accounts.

An orchestrator classifies intent, then specialist agents handle weather, local time, research, persistent memory, scheduled automations, computer use (open public web pages), **mail and calendar** (Google OAuth), general chat, and any **MCP** servers you connect. Talk to it in the terminal or at a **localhost web UI** (`python main.py --serve`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put **one** of these in `.env`:

```
GEMINI_API_KEY=your-gemini-key
```

```
OPENAI_API_KEY=your-openai-key
```

```
ANTHROPIC_API_KEY=your-anthropic-key
```

Optional overrides: `JARVIS_LLM_PROVIDER=gemini|openai|anthropic`, `JARVIS_MODEL=...`, or a generic `JARVIS_API_KEY`. If several provider keys are set, Gemini wins unless you set `JARVIS_LLM_PROVIDER`.

Confirm what Jarvis detected:

```bash
python main.py --status
```

If a Gemini key was ever committed to git history, rotate it in Google AI Studio and use the new value.

## Run

Interactive REPL:

```bash
python main.py
```

Single prompt:

```bash
python main.py --once "What time is it in New York?"
python main.py --once "Research the James Webb Space Telescope"
python main.py --once "Remember that I live in Austin and prefer Celsius"
python main.py --once "Remind me in 10 minutes to stretch"
python main.py --once "Open https://example.com and tell me what it says"
python main.py --once "What's in my inbox?"
python main.py --browse https://example.com
python main.py --automations
python main.py --auth
python main.py --connect google
python main.py --serve
python main.py --serve --open
python main.py --run-due
```

The web UI is **localhost only** (`http://127.0.0.1:8787/`). Same one AI key as the REPL — no extra vendor account. If `.env` has no key yet, the page still loads and tells you what to add.

Type `exit`, `bye`, or `close` to leave the REPL.

## What works with one key

| Capability | How |
| --- | --- |
| Chat / planning | Gemini, OpenAI, or Anthropic |
| General conversation | Chat Agent (same key — greetings, writing, math, advice) |
| Weather | [wttr.in](https://wttr.in) (no extra key) |
| Time | Local timezone database |
| Research | Wikipedia + DuckDuckGo Instant Answers (no extra key) |
| Memory | Local `~/.jarvis/memory.json` — remember facts across sessions (no extra key) |
| Automations | Local `~/.jarvis/automations.json` — reminders and recurring prompts (no extra key) |
| Computer use | Open public http(s) pages, read the text, follow on-page links (no extra key) |
| Mail | Gmail inbox/search after `python main.py --connect google` (OAuth, not an AI key) |
| Calendar | Upcoming Google Calendar events after the same Google login |
| MCP tools | Local stdio servers from `mcp.json` (no extra AI key) |
| Web UI | `python main.py --serve` on 127.0.0.1 (no extra key) |

## MCP connections

Third-party tools plug in through the [Model Context Protocol](https://modelcontextprotocol.io/) without new Python modules and without a second AI vendor key. Copy `mcp.json.example` to `mcp.json` (gitignored) or `~/.jarvis/mcp.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

Jarvis speaks the standard stdio JSON-RPC transport (`initialize`, `tools/list`, `tools/call`). A connected server becomes the **MCP Agent**. Override the config path with `JARVIS_MCP_CONFIG`. Broken servers are skipped so weather, time, and research still work.

`--status` lists configured servers without spawning them.

## Persistent memory

Jarvis keeps personal facts and recent conversation locally so it still knows you after you quit the REPL. Nothing leaves your machine except the one LLM call that uses those facts as context.

- Default file: `~/.jarvis/memory.json`
- Override with `JARVIS_MEMORY_PATH` or `JARVIS_HOME`
- Say **remember**, **forget**, or ask **what do you remember** — the Memory Agent writes the file
- Specialists reuse facts automatically (home city for weather, preferred units, your name)

No extra vendor account. The file is gitignored if you keep it in the project tree.

## Scheduled automations

Reminders and recurring research/weather checks live in a local JSON file. Still one AI key — no cron.com, Twilio, or extra vendor account.

- Default file: `~/.jarvis/automations.json`
- Override with `JARVIS_AUTOMATIONS_PATH` or `JARVIS_HOME`
- Say **remind me in 10 minutes to stretch**, **every morning research the weather**, **list automations**, or **cancel** a job id
- The REPL fires due jobs between turns
- Hook system cron (or Task Scheduler) to `python main.py --run-due` so jobs still run while the REPL is closed
- `python main.py --automations` lists jobs without needing an API key
- Two kinds: **remind** (print a message) and **run** (send a stored prompt back through Jarvis)

No extra vendor account. Recurring jobs use `every 30 minutes` or `daily at 8:00`.

## Computer use

Jarvis can operate a **local public-web browser** with the same AI key — no Playwright account, Browserbase, or extra vendor.

- Say **open https://example.com**, **browse python.org**, **what's on this page**, or **follow the Docs link**
- The Computer Agent fetches the page, strips scripts/styles, and returns readable text plus top links
- Follow-up **follow** / **click** uses links from the last opened page in this process
- `python main.py --browse https://example.com` reads a page without an API key
- Only public `http`/`https` URLs are allowed. Localhost, private LAN, and cloud-metadata addresses are blocked
- JavaScript-heavy apps may return little text; encyclopedic questions without a URL still go to the Research Agent

No extra vendor account. This is the first computer-use layer; desktop mouse/keyboard control can come later.

## Mail and calendar (connect via auth)

Gmail and Google Calendar are optional. They use **OAuth**, not a second AI vendor key.

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project (or reuse one).
2. Enable **Gmail API** and **Google Calendar API**.
3. Create an OAuth client ID of type **Desktop app**.
4. Put the client id in `.env`:

```
GOOGLE_OAUTH_CLIENT_ID=....apps.googleusercontent.com
```

Web clients may also set `GOOGLE_OAUTH_CLIENT_SECRET`. Then:

```bash
python main.py --connect google
```

Jarvis starts a localhost callback, opens the Google consent screen, and stores tokens in `~/.jarvis/auth.json` (mode 0600). Scopes are **readonly** mail and calendar plus email identity.

```bash
python main.py --auth
python main.py --once "What's in my inbox?"
python main.py --once "What's on my calendar today?"
python main.py --disconnect google
```

`--auth`, `--connect`, and `--disconnect` do not need an AI API key. If Google is not connected, the Mail and Calendar agents tell you to run `--connect google` instead of failing the rest of Jarvis.

Override the token file with `JARVIS_AUTH_PATH` or `JARVIS_HOME`. The file is gitignored.

## Local web UI

The REPL is optional. After one AI key is in `.env`:

```bash
python main.py --serve --open
```

Jarvis serves a chat page at `http://127.0.0.1:8787/` (override with `--port` / `--host`). The sidebar shows the detected LLM, built-in tools, memory, automations, Google auth, and MCP. Chat goes through the same orchestrator as the terminal — weather, research, remember, reminders, browse, inbox, calendar.

- No extra API key and no cloud UI vendor
- Binds to loopback so the assistant is not on your LAN
- Works without a key too: the page explains what to put in `.env`, and chat stays paused
- Status/health are JSON at `/api/status` and `/api/health`

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Set `JARVIS_DEBUG=1` to print LLM prompts while iterating.

## Project layout

- `main.py` — CLI (`--once`, `--status`, `--automations`, `--run-due`, `--browse`, `--auth`, `--connect`, `--disconnect`, `--serve`)
- `src/assistant.py` — default weather, time, research, memory, automation, computer, mail, calendar, chat, and optional MCP agents
- `src/auth/` — local OAuth token store and Google mail/calendar clients
- `src/auth/` — local OAuth token store and Google mail/calendar clients
- `src/automation/` — local job store, schedule parser, and due-job runner
- `src/computer/` — public-web fetch, HTML extract, and in-process link following
- `src/config.py` — one-key provider detection
- `src/llm.py` — Gemini / OpenAI / Anthropic client
- `src/mcp/` — stdio MCP client and `mcp.json` loader
- `src/memory/` — local persistent facts and recent turns
- `src/orchestrator.py` — routes a request, loops specialists, then answers
- `src/tools/` — weather, time, research, memory, automation, computer, mail, calendar, MCP adapters
- `src/ui/` — localhost web chat UI (`--serve`)
- `tests/` — unit tests that do not need live API keys

## Roadmap

These are the next layers toward a drop-in assistant that also handles auth, automations, and computer use:

1. ~~MCP client so third-party tools can be connected without new Python modules~~ (stdio `mcp.json` client)
2. ~~General chat agent so non-tool questions are not forced into weather/time/research~~
3. ~~Multi-step orchestration so compound requests finish instead of stopping on the first tool result~~
4. ~~Persistent memory across sessions~~ (local `~/.jarvis/memory.json`)
5. ~~Scheduled automations~~ (local `~/.jarvis/automations.json`, `--run-due`)
6. ~~Computer use / browser control~~ (public-web pages + link following; no extra key)
7. ~~OAuth for mail / calendar~~ (Google PKCE + localhost; readonly Gmail and Calendar)
8. ~~Thin local web UI~~ (`python main.py --serve` on 127.0.0.1)
9. Desktop / JS-capable computer use (Playwright or screenshot+input)
10. Provider fallback if the first AI key fails
11. Microsoft / Outlook OAuth using the same auth store
