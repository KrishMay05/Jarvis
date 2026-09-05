# Jarvis

A personal assistant you can run locally. Drop in **one AI API key** (Gemini, OpenAI, or Anthropic) and the built-in tools work — no weather key, no search key, no extra accounts.

An orchestrator classifies intent, then specialist agents handle weather, local time, research, persistent memory, general chat, and any **MCP** servers you connect.

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
```

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
| MCP tools | Local stdio servers from `mcp.json` (no extra AI key) |

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

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Set `JARVIS_DEBUG=1` to print LLM prompts while iterating.

## Project layout

- `main.py` — CLI (`--once`, `--status`)
- `src/assistant.py` — default weather, time, research, memory, chat, and optional MCP agents
- `src/config.py` — one-key provider detection
- `src/llm.py` — Gemini / OpenAI / Anthropic client
- `src/mcp/` — stdio MCP client and `mcp.json` loader
- `src/memory/` — local persistent facts and recent turns
- `src/orchestrator.py` — routes a request, loops specialists, then answers
- `src/tools/` — weather, time, research, memory, MCP adapters
- `tests/` — unit tests that do not need live API keys

## Roadmap

These are the next layers toward a drop-in assistant that also handles auth, automations, and computer use:

1. ~~MCP client so third-party tools can be connected without new Python modules~~ (stdio `mcp.json` client)
2. ~~General chat agent so non-tool questions are not forced into weather/time/research~~
3. ~~Multi-step orchestration so compound requests finish instead of stopping on the first tool result~~
4. ~~Persistent memory across sessions~~ (local `~/.jarvis/memory.json`)
5. Scheduled automations (reminders, recurring research)
6. OAuth for mail / calendar instead of extra API keys
7. Computer use / browser control
