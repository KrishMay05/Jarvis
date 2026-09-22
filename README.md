# Jarvis

A personal assistant you can run locally. Drop in **one AI API key** (Gemini, OpenAI, or Anthropic) and the built-in tools work — no weather key, no search key, no extra accounts.

An orchestrator classifies intent, then specialist agents handle weather, local time, research, persistent memory, scheduled automations, computer use (open public web pages), **mail and calendar** (Google OAuth from the CLI or the localhost UI), general chat, and any **MCP** servers you connect. Talk to it in the terminal or at a **localhost web UI** (`python main.py --serve`). Refreshing the page restores recent conversation from the same local memory file as the REPL. Clear conversation when you want a fresh thread — remembered facts stay. The sidebar lists remembered facts, scheduled jobs, and MCP servers so you can add, forget, pause, cancel, or connect them without chatting.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put **one** of these in `.env`, or skip the file and paste the key in the localhost UI (`python main.py --serve`):

```
GEMINI_API_KEY=your-gemini-key
```

```
OPENAI_API_KEY=your-openai-key
```

```
ANTHROPIC_API_KEY=your-anthropic-key
```

Optional overrides: `JARVIS_LLM_PROVIDER=gemini|openai|anthropic`, `JARVIS_MODEL=...`, or a generic `JARVIS_API_KEY`. If several provider keys are set, Gemini is the primary unless you set `JARVIS_LLM_PROVIDER`. Extra keys are optional backups — if the primary provider is down, rate-limited, or rejects the key, Jarvis fails over automatically. One key is still enough. Set `JARVIS_LLM_FAILOVER=0` to disable that.

Confirm what Jarvis detected (including any optional backup LLM):

```bash
python main.py --status
```

`--status` does not need a live model call. If two provider keys are in `.env`, the banner lists a **Fallback LLM** used only when the primary fails.

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

The web UI is **localhost only** (`http://127.0.0.1:8787/`). Same one AI key as the REPL — no extra vendor account. If `.env` has no key yet, the page still loads: paste a Gemini, OpenAI, or Anthropic key in the sidebar. Chat unlocks immediately and the key is written to local `.env` (gitignored, mode 0600). No restart.

Type `exit`, `bye`, or `close` to leave the REPL.

## What works with one key

| Capability | How |
| --- | --- |
| Chat / planning | Gemini, OpenAI, or Anthropic (optional extra key is a backup if the first provider fails) |
| General conversation | Chat Agent (same key — greetings, writing, math, advice) |
| Weather | [wttr.in](https://wttr.in) (no extra key) |
| Time | Local timezone database |
| Research | Wikipedia, named public sites, DuckDuckGo, then Stack Overflow (no extra key) |
| Memory | Local `~/.jarvis/memory.json` — remember facts across sessions; inspect or forget them in the web UI; the chat log restores on refresh and can be cleared without deleting facts (no extra key) |
| Automations | Local `~/.jarvis/automations.json` — reminders and recurring prompts; `--serve` fires them in the background and the sidebar can add/pause/cancel jobs (no extra key) |
| Computer use | Open public http(s) pages, read the text, follow on-page links (no extra key) |
| Mail | Gmail inbox/search and full message bodies after Connect Google in the web UI or `python main.py --connect google` (OAuth, not an AI key) |
| Calendar | Today's / tomorrow's / this week's Google Calendar agenda after the same Google login |
| MCP tools | Local stdio servers from `mcp.json` or the localhost UI MCP editor (no extra AI key) |
| Web UI | `python main.py --serve` on 127.0.0.1 (paste the AI key or Google OAuth client ID in the sidebar or use `.env`; Connect Google there too; chat streams live; refresh restores recent turns; Clear conversation starts a fresh thread) |

## MCP connections

Third-party tools plug in through the [Model Context Protocol](https://modelcontextprotocol.io/) without new Python modules and without a second AI vendor key. Add servers in the localhost UI sidebar, or copy `mcp.json.example` to `mcp.json` (gitignored) or `~/.jarvis/mcp.json`:

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

In the localhost UI, the **MCP** sidebar lists saved servers. Add, disable, enable, remove, or reload them there without chatting and without an AI key. Env values stay on disk (mode 0600) and are not returned by the API. If chat is already unlocked, Jarvis reconnects the MCP Agent immediately — no restart.

`--status` lists configured servers without spawning them.

## Persistent memory

Jarvis keeps personal facts and recent conversation locally so it still knows you after you quit the REPL. Nothing leaves your machine except the one LLM call that uses those facts as context.

- Default file: `~/.jarvis/memory.json`
- Override with `JARVIS_MEMORY_PATH` or `JARVIS_HOME`
- Say **remember**, **forget**, or ask **what do you remember** — the Memory Agent writes the file
- In the localhost UI, the **Memory** sidebar lists facts. Add or forget them there without chatting and without an AI key
- Refreshing the localhost UI restores recent conversation turns from the same file (no extra API key; chat can stay paused)
- **Clear conversation** in the localhost UI (or say **clear conversation** / **forget the chat**) drops those turns without deleting remembered facts. No extra API key.
- Specialists reuse facts automatically (home city for weather, preferred units, your name)

No extra vendor account. The file is gitignored if you keep it in the project tree.

## Scheduled automations

Reminders and recurring research/weather checks live in a local JSON file. Still one AI key — no cron.com, Twilio, or extra vendor account.

- Default file: `~/.jarvis/automations.json`
- Override with `JARVIS_AUTOMATIONS_PATH` or `JARVIS_HOME`
- Say **remind me in 10 minutes to stretch**, **every morning research the weather**, **list automations**, or **cancel** a job id
- The localhost UI **Automations** sidebar lists jobs. Schedule, pause, resume, or cancel them without chatting (reminders still need no AI key; `run` jobs use the same key when they fire)
- The REPL fires due jobs between turns
- **`python main.py --serve` fires due jobs in the background** while the localhost UI is open (every 15 seconds). Reminders do not need an AI key; `run` jobs use the same key as chat. Due reports show up in the chat log without sending another message
- Hook system cron (or Task Scheduler) to `python main.py --run-due` only if the UI and REPL are both closed
- `python main.py --automations` lists jobs without needing an API key
- Two kinds: **remind** (print a message) and **run** (send a stored prompt back through Jarvis)

No extra vendor account. Recurring jobs use `every 30 minutes` or `daily at 8:00`.

## Research

Encyclopedic questions should work with the same AI key — no Brave, SerpAPI, or search-vendor account.

1. Wikipedia first (several search hits; disambiguation pages are skipped)
2. If you named a public site (`wttr.in`, `https://example.com/docs`), Jarvis reads that page with the same SSRF-safe browser as computer use
3. DuckDuckGo Instant Answers when those miss
4. A public DuckDuckGo HTML search when Instant Answers are empty, plus a short extract from the top public page
5. Stack Overflow excerpts for how-to questions (no extra key; used when search pages are empty or blocked)

If every source misses, Jarvis says so instead of inventing a citation.

## Computer use

Jarvis can operate a **local public-web browser** with the same AI key — no Playwright account, Browserbase, or extra vendor.

- Say **open https://example.com**, **browse python.org**, **what's on this page**, or **follow the Docs link**
- The Computer Agent fetches the page, strips scripts/styles, and returns readable text plus top links
- Follow-up **follow** / **click** uses links from the last opened page in this process
- `python main.py --browse https://example.com` reads a page without an API key
- Only public `http`/`https` URLs are allowed. Localhost, private LAN, and cloud-metadata addresses are blocked
- JavaScript-heavy apps may return little text; encyclopedic questions without a URL still go to the Research Agent, which can also fall back to a public web search

No extra vendor account. This is the first computer-use layer; desktop mouse/keyboard control can come later.

## Mail and calendar (connect via auth)

Gmail and Google Calendar are optional. They use **OAuth**, not a second AI vendor key.

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project (or reuse one).
2. Enable **Gmail API** and **Google Calendar API**.
3. Create an OAuth client ID of type **Desktop app**.
4. Paste the client id in the localhost UI sidebar (**Save Google client**) — no `.env` edit and no restart — or put it in `.env`:

```
GOOGLE_OAUTH_CLIENT_ID=....apps.googleusercontent.com
```

Web clients may also paste an optional `GOOGLE_OAUTH_CLIENT_SECRET` (or set it in `.env`). Then connect from either place:

```bash
python main.py --serve --open
```

Click **Connect Google** in the sidebar. Jarvis uses the same localhost UI as the OAuth callback, so you do not leave the assistant. Or from the terminal:

```bash
python main.py --connect google
```

Jarvis starts a localhost callback, opens the Google consent screen, and stores tokens in `~/.jarvis/auth.json` (mode 0600). Scopes are **readonly** mail and calendar plus email identity.

Inbox listings include message ids. Ask Jarvis to **read** one (or `from:ada`) to open the full body, not just the snippet. **Today**, **tomorrow**, and **this week** are local-day calendar windows — “what's on my calendar today?” is today's agenda, not every upcoming event.

```bash
python main.py --auth
python main.py --once "What's in my inbox?"
python main.py --once "Read the latest email from Ada"
python main.py --once "What's on my calendar today?"
python main.py --disconnect google
```

`--auth`, `--connect`, `--disconnect`, pasting a Google OAuth client ID, and Connect Google in the web UI do not need an AI API key. If Google is not connected, the Mail and Calendar agents tell you to connect instead of failing the rest of Jarvis.

Override the token file with `JARVIS_AUTH_PATH` or `JARVIS_HOME`. The file is gitignored.

## Local web UI

The REPL is optional. Start the UI even before you have a key:

```bash
python main.py --serve --open
```

Jarvis serves a chat page at `http://127.0.0.1:8787/` (override with `--port` / `--host`). The sidebar shows the detected LLM, built-in tools, memory, automations, Google auth, and MCP. **Paste one AI key** there to unlock chat without editing `.env` or restarting. **Paste a Google OAuth client ID** in the Auth sidebar the same way, then **Connect Google** — no `.env` edit and no restart. **Remember facts, schedule automations, and connect MCP servers** in that same sidebar — no chat phrasing and no extra API key. **Connect Google** and **Disconnect** live there too — same OAuth as `--connect google`. Chat goes through the same orchestrator as the terminal — weather, research, remember, reminders, browse, inbox, calendar, MCP tools. **Replies stream into the chat log** (planning, specialist steps, then tokens) so Send is not frozen. **Refresh restores recent conversation** from local memory — the same turns the REPL already kept. **Clear conversation** empties that log and the on-disk turns without deleting remembered facts. **Due reminders appear in the chat log on their own** while `--serve` is running; you do not need to send a message or set up system cron for that.

- No extra API key and no cloud UI vendor
- Binds to loopback so the assistant is not on your LAN
- Works without a key too: paste a Gemini / OpenAI / Anthropic key in the sidebar (saved to local `.env`), paste a Google OAuth client ID, remember facts, schedule reminders, add MCP servers, or keep chat paused and still connect Google
- Status/health are JSON at `/api/status` and `/api/health` (including optional LLM fallbacks)
- Chat is `POST /api/chat` (full reply) or `POST /api/chat/stream` (SSE: status, specialist steps, tokens, due jobs)
- Recent conversation is JSON at `GET /api/chat/history` (restored into the chat log on refresh; no extra API key)
- Clearing conversation is `POST /api/chat/history/clear` on localhost only (facts stay; no extra API key)
- Memory facts are JSON at `/api/memory` (`POST` to add, `/api/memory/forget` to drop)
- Automations are JSON at `/api/automations` (`POST` to add, `/cancel`, `/pause`, `/enable`)
- MCP servers are JSON at `/api/mcp` (`POST` to add/update, `/remove`, `/disable`, `/enable`, `/reload`)
- Due automations are JSON at `/api/due` (consumed by the page poll)
- Saving a key is `POST /api/key` on localhost only — the key is never returned in API responses
- Saving a Google OAuth client ID is `POST /api/auth/google/config` on localhost only — the client ID and secret are never returned in API responses
- Google OAuth callback is `/oauth/google/callback` on the same localhost server

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
- `src/automation/` — local job store, schedule parser, and due-job runner
- `src/computer/` — public-web fetch, HTML extract, and in-process link following
- `src/config.py` — one-key provider detection (extra keys are optional LLM backups)
- `src/llm.py` — Gemini / OpenAI / Anthropic client with automatic provider failover and streaming
- `src/mcp/` — stdio MCP client, `mcp.json` loader, and localhost UI save/reload
- `src/memory/` — local persistent facts and recent turns
- `src/orchestrator.py` — routes a request, loops specialists, then answers
- `src/tools/` — weather, time, research, memory, automation, computer, mail, calendar, MCP adapters
- `src/ui/` — localhost web UI (`--serve`; streaming replies; restored history; clear conversation; background automation ticker; memory, automation, and MCP editors; paste AI key or Google OAuth client ID)
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
9. ~~Connect Google from the web UI~~ (same localhost server as the OAuth callback)
10. ~~Provider fallback if the first AI key fails~~ (optional extra Gemini/OpenAI/Anthropic key; one key still enough)
11. ~~Richer research when Instant Answers are empty~~ (Wikipedia candidates + public web search, still no extra key)
12. ~~Paste an AI key from the localhost UI~~ (no `.env` edit, no restart; still one key)
13. ~~Background automation ticker in `--serve`~~ (due reminders appear in the UI without cron or another chat)
14. ~~Memory and automation editors in the localhost UI~~ (list/add/forget facts and schedule/pause/cancel jobs without chatting)
15. ~~MCP editor in the localhost UI~~ (add/disable/remove/reload stdio servers without editing `mcp.json` by hand)
16. ~~Read full Gmail bodies and calendar today/tomorrow/week windows~~ (same Google OAuth; still readonly)
17. ~~Streaming replies in the localhost UI~~ (SSE over the same one AI key; tool steps stay visible)
18. ~~Restore chat history in the localhost UI on refresh~~ (same `memory.json` turns as the REPL; no extra key)
19. ~~Paste a Google OAuth client ID from the localhost UI~~ (no `.env` edit, no restart; still not a second AI key)
20. ~~Clear conversation in the localhost UI~~ (same `memory.json` turns as the REPL; facts stay; no extra key)
21. Desktop / JS-capable computer use (Playwright or screenshot+input)
22. Microsoft / Outlook OAuth using the same auth store
