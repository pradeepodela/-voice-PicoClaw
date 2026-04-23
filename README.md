# Voice PicoClaw — RPI AI Voice Agent

A voice-first AI assistant built for Raspberry Pi. Talk to it naturally, it responds through speakers, remembers past conversations, and uses tools to fetch live data. Backed by [OpenRouter](https://openrouter.ai) (free LLMs) and [PicoClaw](https://github.com/sipeed/picoclaw) for web search, cron reminders, and agentic tasks.

---

## Features

- **Voice pipeline** — webrtcvad (VAD) → Groq Whisper (STT) → LLM → edge-tts (TTS)
- **Streaming responses** — sentences are streamed to TTS as they generate, so the first word plays in ~300ms
- **Persistent memory** — rolling context window with LLM-summarized long-term memory saved to disk
- **Extensible tools** — add new tools with a single `@tool` decorator; built-in: weather, calculator, datetime, reminders
- **PicoClaw integration** — route all queries through PicoClaw (web search, code execution, cron jobs) or use it as a callable tool
- **Cron reminders** — spoken reminders scheduled via PicoClaw's cron system, delivered through your speakers
- **Text mode** — run without any audio hardware for testing: `python app.py --text`

---

## Architecture

```
You (voice) ──► VAD ──► STT (Groq Whisper)
                              │
                              ▼
                    agent/core.py (Agent)
                    ┌─────────────────────┐
                    │  Memory (2-tier)     │
                    │  Tool Registry       │
                    │  LLM Client          │◄── OpenRouter (free models)
                    │  PicoClaw Router     │◄── picoclaw agent (web, cron, code)
                    └─────────────────────┘
                              │
                              ▼
                    TTS (edge-tts) ──► Speaker

PicoClaw cron job fires ──► speak skill ──► curl POST :7700 ──► TTS speaks it
```

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/pradeepodela/-voice-PicoClaw.git
cd voice-PicoClaw
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set API keys

```bash
cp .env.example .env
# Edit .env and fill in:
#   OPENROUTER_API_KEY=sk-or-v1-...   (free at openrouter.ai)
#   GROQ_API_KEY=gsk_...              (free at console.groq.com)
```

### 3. Install PicoClaw

```bash
bash scripts/setup_picoclaw.sh
picoclaw onboard   # follow prompts to connect Telegram (optional)
```

### 4. Run

**Text mode (no microphone needed):**
```bash
python app.py --text
```

**Voice mode:**
```bash
# Terminal 1 — keep the gateway running for cron reminders
./scripts/start_gateway.sh

# Terminal 2 — start the voice agent
python app.py
```

---

## Configuration

All settings live in `config.yaml`. Key options:

| Section | Key | Description |
|---|---|---|
| `llm` | `model` | OpenRouter model (any free model that supports tool calling) |
| `voice.stt` | `engine` | `groq` (fast, free API) or `local` (faster-whisper, no internet) |
| `voice.tts` | `engine` | `edge` (Microsoft neural, online) or `pyttsx3` (offline) |
| `voice.vad` | `aggressiveness` | 0–3; 3 = most aggressive noise rejection |
| `picoclaw` | `use_as_primary` | `true` = all queries go through PicoClaw; `false` = OpenRouter primary |
| `picoclaw` | `auto_start_gateway` | `true` = auto-start gateway on launch; `false` = manage it yourself |

### Recommended free models (OpenRouter)

```yaml
llm:
  model: "google/gemini-2.0-flash-exp:free"   # fast, tool calling works
  # model: "openai/gpt-oss-120b:free"
  # model: "meta-llama/llama-3.1-8b-instruct:free"
```

---

## Adding Tools

Create a file in `tools/` and register it with `@tool`:

```python
# tools/my_tool.py
from agent.tool_registry import tool

@tool(
    name="my_tool",
    description="What this tool does",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Input"}
        },
        "required": ["query"],
    },
)
async def my_tool(query: str) -> str:
    return f"Result for: {query}"
```

Then import it in `tools/__init__.py`:

```python
from . import calculator, datetime_tool, weather, picoclaw_tool, reminder_tool, my_tool
```

---

## Cron Reminders via PicoClaw

Spoken reminders are delivered from PicoClaw's cron system through a local HTTP server on port 7700.

**How it works:**
1. Say *"Remind me in 10 minutes to take medication"*
2. PicoClaw creates a cron job (gateway must be running)
3. When the job fires, PicoClaw's agent uses the `speak` skill to POST to `http://localhost:7700/remind`
4. The voice agent's TTS speaks it through your speakers

**Manual cron job (via PicoClaw):**
```bash
picoclaw cron add --every 600 --name reminder_meds \
  --message "Use the speak skill to say: 'Time to take your medication'. Then disable cron job named reminder_meds."
```

**Trigger a reminder directly (for testing):**
```bash
curl -X POST http://localhost:7700/remind -d "Hello from cron"
```

---

## CLI Flags

```
python app.py [flags]

  --text          Force text/CLI mode (no mic/speaker needed)
  --voice         Force voice mode
  --verbose       Show tool call details
  --picoclaw      Route all queries through PicoClaw (overrides config)
  --no-picoclaw   Force OpenRouter routing (overrides config)
  --no-gateway    Skip picoclaw gateway auto-start
  --config FILE   Use a different config file (default: config.yaml)
```

**In-session commands (text mode):**
```
/reset    Clear conversation session
/tools    List loaded tools
/memory   Show conversation history
```

---

## Project Structure

```
├── app.py                  Entry point (voice + text modes)
├── config.yaml             All configuration
├── .env.example            API key template
├── agent/
│   ├── core.py             Agent loop, tool calls, PicoClaw routing
│   ├── llm_client.py       OpenRouter client (streaming)
│   ├── memory.py           Two-tier memory (rolling + summarized)
│   ├── tool_registry.py    @tool decorator and registry
│   ├── picoclaw_gateway.py Gateway subprocess manager
│   └── reminder_server.py  HTTP server for cron → TTS delivery
├── tools/
│   ├── calculator.py
│   ├── datetime_tool.py
│   ├── weather.py
│   ├── picoclaw_tool.py    Calls picoclaw agent as a tool
│   └── reminder_tool.py    Scheduled spoken reminders
├── voice/
│   ├── vad.py              Voice activity detection (webrtcvad)
│   ├── stt.py              Speech-to-text (Groq Whisper / faster-whisper)
│   └── tts.py              Text-to-speech (edge-tts / pyttsx3)
└── scripts/
    ├── setup_picoclaw.sh   Install PicoClaw binary
    └── start_gateway.sh    Safe gateway launcher (prevents duplicate instances)
```

---

## Requirements

- Python 3.9+
- [OpenRouter API key](https://openrouter.ai) (free tier available)
- [Groq API key](https://console.groq.com) (free tier, for Whisper STT)
- [PicoClaw](https://github.com/sipeed/picoclaw) binary in PATH
- macOS or Linux (Raspberry Pi OS recommended for deployment)

For voice mode, also: a microphone and speaker connected to the Pi.
