"""
RPI Agent — entry point
Usage:
    python app.py              # voice mode (default if voice.enabled=true)
    python app.py --text       # text/CLI mode
    python app.py --text --verbose
"""

import argparse
import asyncio
import os
import sys

import yaml
from dotenv import load_dotenv


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_agent(config: dict, verbose: bool = False):
    from agent.llm_client import LLMClient
    from agent.memory import Memory
    from agent.tool_registry import get_registry
    from agent.core import Agent

    # Import tools so their @tool decorators register them
    import tools  # noqa: F401

    llm = LLMClient(config["llm"])
    registry = get_registry()
    memory = Memory(config["memory"], llm)
    agent_cfg = config.get("agent", {})

    pc_cfg = config.get("picoclaw", {})
    agent = Agent(
        llm=llm,
        memory=memory,
        tools=registry,
        system_prompt=agent_cfg.get("system_prompt", "You are a helpful assistant."),
        agent_name=agent_cfg.get("name", "RPI Agent"),
        verbose=verbose,
        picoclaw_primary=pc_cfg.get("use_as_primary", False),
        picoclaw_binary=pc_cfg.get("binary", "picoclaw"),
        picoclaw_timeout=pc_cfg.get("timeout", 90),
        picoclaw_history_turns=pc_cfg.get("history_turns", 4),
    )
    return agent


# ---------------------------------------------------------------------------
# Text mode
# ---------------------------------------------------------------------------

async def run_text_mode(agent, verbose: bool = False) -> None:
    name = agent.name
    tools_loaded = agent.tools.names()
    routing = "PicoClaw primary" if agent.picoclaw_primary else f"OpenRouter ({agent.llm.model})"
    print(f"\n{name} — text mode  [{routing}]")
    print(f"Tools loaded: {', '.join(tools_loaded) if tools_loaded else 'none'}")

    # Text-mode reminder fallback — print to terminal instead of speaking
    from tools.reminder_tool import set_tts_callback
    async def _print_reminder(msg: str) -> None:
        print(f"\n*** REMINDER: {msg} ***\n")
    set_tts_callback(_print_reminder)
    print("Type 'quit', 'exit', or Ctrl-C to stop.")
    print("Commands: /reset (clear session), /tools (list tools), /memory (show history)\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            agent.memory.save()
            break

        if not user_input:
            continue

        # Built-in commands
        if user_input.lower() in ("quit", "exit"):
            print("Bye!")
            agent.memory.save()
            break
        if user_input == "/reset":
            agent.reset_session()
            print("[Session cleared]")
            continue
        if user_input == "/tools":
            names = agent.tools.names()
            print("[Tools]:", ", ".join(names) if names else "none")
            continue
        if user_input == "/memory":
            msgs = agent.memory.messages
            if not msgs:
                print("[Memory is empty]")
            for m in msgs:
                print(f"  {m['role']:10} {m['content'][:80]}")
            continue

        try:
            print(f"{agent.name}: ", end="", flush=True)
            reply = await agent.chat(user_input)
            print(reply)
        except Exception as e:
            print(f"\n[Error] {e}")


# ---------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------

async def run_voice_mode(agent, config: dict) -> None:
    from voice.vad import VADRecorder
    from voice.stt import SpeechToText
    from voice.tts import TextToSpeech

    voice_cfg = config["voice"]
    vad = VADRecorder(voice_cfg["vad"])
    stt = SpeechToText(voice_cfg["stt"])
    tts = TextToSpeech(voice_cfg["tts"])

    # Give the reminder tool a reference to TTS so it can speak aloud
    from tools.reminder_tool import set_tts_callback
    set_tts_callback(tts.speak)

    wake_word_enabled = voice_cfg.get("wake_word", {}).get("enabled", False)
    wake_phrase = voice_cfg.get("wake_word", {}).get("word", "hey pi")

    stt_label = f"groq/{voice_cfg['stt'].get('groq_model','whisper-large-v3-turbo')}" \
        if voice_cfg["stt"].get("engine", "groq") == "groq" \
        else f"local/{voice_cfg['stt'].get('model','tiny')}"

    routing = "PicoClaw primary" if agent.picoclaw_primary else f"OpenRouter ({agent.llm.model})"
    print(f"\n{agent.name} — voice mode (streaming)  [{routing}]")
    print(f"STT: {stt_label} | TTS: {voice_cfg['tts']['engine']}")
    print(f"Wake word: {'enabled (' + wake_phrase + ')' if wake_word_enabled else 'disabled'}")
    print("Press Ctrl-C to stop.\n")

    await tts.speak(f"Hello! I'm {agent.name}. How can I help you?")

    while True:
        try:
            if wake_word_enabled:
                print("[Waiting for wake word...]", flush=True)
                detected = vad.wait_for_wake_word(wake_phrase, stt.transcribe)
                if not detected:
                    continue

            print("[Listening...]", flush=True)
            audio = vad.record(
                timeout=15.0,
                status_cb=lambda s: print(f"[{s}]", flush=True),
            )

            if audio is None:
                if not wake_word_enabled:
                    # Brief pause so we don't spin 100% CPU on silence
                    await asyncio.sleep(0.1)
                continue

            print("[Transcribing...]", flush=True)
            text = stt.transcribe(audio)

            if not text:
                continue

            print(f"You: {text}")

            # Quick exit command
            if text.lower().strip() in ("exit", "quit", "goodbye", "bye"):
                await tts.speak("Goodbye!")
                agent.memory.save()
                break

            print(f"{agent.name}: ", end="", flush=True)

            # Pipe streaming sentences to TTS in parallel with generation.
            # The inner generator prints each sentence as it arrives so the
            # terminal mirrors what's being spoken in real time.
            async def speaking_sentences():
                async for sentence in agent.stream_sentences(text):
                    print(sentence, end=" ", flush=True)
                    yield sentence

            await tts.speak_stream(speaking_sentences())
            print()  # newline after streamed output

        except KeyboardInterrupt:
            print("\n[Stopped]")
            agent.memory.save()
            break
        except Exception as e:
            print(f"[Error] {e}")
            await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="RPI Agent")
    parser.add_argument("--text", action="store_true", help="Force text/CLI mode")
    parser.add_argument("--voice", action="store_true", help="Force voice mode")
    parser.add_argument("--verbose", action="store_true", help="Show tool call details")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--picoclaw", action="store_true", help="Route all queries through PicoClaw (overrides config)")
    parser.add_argument("--no-picoclaw", action="store_true", help="Force OpenRouter routing (overrides config)")
    parser.add_argument("--no-gateway", action="store_true", help="Don't auto-start picoclaw gateway (cron jobs won't run)")
    args = parser.parse_args()

    load_dotenv()

    if not os.getenv("OPENROUTER_API_KEY"):
        print("Error: OPENROUTER_API_KEY not set.")
        print("Copy .env.example to .env and add your key.")
        sys.exit(1)

    config = load_config(args.config)

    # CLI flags override config.yaml
    if args.picoclaw:
        config.setdefault("picoclaw", {})["use_as_primary"] = True
    elif args.no_picoclaw:
        config.setdefault("picoclaw", {})["use_as_primary"] = False

    agent = build_agent(config, verbose=args.verbose)

    pc_cfg = config.get("picoclaw", {})

    # Auto-start picoclaw gateway so cron jobs fire (skips if one already running).
    gateway = None
    want_gateway = (
        pc_cfg.get("use_as_primary", False)
        and pc_cfg.get("auto_start_gateway", True)
        and not args.no_gateway
    )
    if want_gateway:
        from agent.picoclaw_gateway import GatewayManager
        gateway = GatewayManager(
            binary=pc_cfg.get("binary", "picoclaw"),
            verbose=args.verbose,
        )
        await gateway.start()

    # Start reminder HTTP server so PicoClaw cron jobs can speak to us.
    # PicoClaw cron shell command:
    #   curl -s -X POST http://localhost:7700/remind -d "your message"
    from agent.reminder_server import ReminderServer
    from voice.tts import TextToSpeech as _TTS

    _reminder_tts = _TTS(config.get("voice", {}).get("tts", {}))
    reminder_server = ReminderServer(
        port=pc_cfg.get("reminder_port", 7700),
        on_reminder=_reminder_tts.speak,
        verbose=args.verbose,
    )
    await reminder_server.start()

    try:
        voice_enabled = config.get("voice", {}).get("enabled", True)
        use_voice = args.voice or (voice_enabled and not args.text)

        if use_voice:
            try:
                import webrtcvad, sounddevice, edge_tts  # noqa
                await run_voice_mode(agent, config)
            except ImportError as e:
                print(f"[Voice deps missing: {e}] Falling back to text mode.")
                await run_text_mode(agent, args.verbose)
        else:
            await run_text_mode(agent, args.verbose)
    finally:
        await reminder_server.stop()
        if gateway:
            await gateway.stop()


if __name__ == "__main__":
    asyncio.run(main())
