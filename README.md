# Jarvis

A small multi-agent assistant inspired by Iron Man's Jarvis. An orchestrator classifies intent, then hands work to specialist agents (weather and local time) that can call tools.

## Why this layout

The first commit in this repository accidentally included a local virtualenv and a `.env` file. Those were removed. This restore keeps the original agent design, but at the repo root, without secrets or `venv/`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add a Gemini key to `.env`. For weather lookups, also add an [OpenWeatherMap](https://openweathermap.org/api) key. LLM calls use the current `google-genai` SDK.

```
GEMINI_API_KEY=your-gemini-key
OPENWEATHER_API_KEY=your-openweather-key
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
```

Type `exit`, `bye`, or `close` to leave the REPL.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## Project layout

- `main.py` — CLI entry point
- `src/orchestrator.py` — routes a request to the right agent
- `src/agent.py` — tool-using specialist
- `src/tools/` — weather and time tools
- `tests/` — unit tests that do not need live API keys
