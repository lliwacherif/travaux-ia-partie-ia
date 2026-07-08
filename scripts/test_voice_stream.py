"""Quick terminal test for the real-time voice transcription WebSocket.

Usage:
    1. Start the backend:  uvicorn app.main:app --reload
    2. Run this script:    python scripts/test_voice_stream.py

    It connects to the WebSocket, captures your microphone for ~30 seconds,
    streams the audio, and prints transcriptions in real-time.

    Requirements: pip install sounddevice numpy websockets
"""

import asyncio
import base64
import json
import sys
import struct
import threading

try:
    import sounddevice as sd
except ImportError:
    print("ERROR: sounddevice is required.  Install it with:")
    print("  pip install sounddevice")
    sys.exit(1)

import websockets

# ---- Config ----------------------------------------------------------------
WS_URL = "ws://localhost:8000/api/v1/voice/stream"
SAMPLE_RATE = 24000      # 24 kHz  (required by OpenAI)
CHANNELS = 1             # mono
CHUNK_FRAMES = 4096      # ~170 ms per chunk at 24 kHz
RECORD_SECONDS = 30      # how long to record before auto-stop
# ----------------------------------------------------------------------------


async def main():
    # Grab a reference to the running event loop BEFORE spawning threads.
    loop = asyncio.get_running_loop()

    print(f"Connecting to {WS_URL} ...")
    try:
        async with websockets.connect(WS_URL) as ws:
            print("Connected! Waiting for session_ready ...")

            # --- Wait for session_ready ---
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                print(f"  << {msg['type']}")
                if msg["type"] == "session_ready":
                    break
                if msg["type"] == "error":
                    print(f"  ERROR: {msg['message']}")
                    return

            print()
            print("=" * 60)
            print(f"  SESSION READY - SPEAK NOW!  (recording for {RECORD_SECONDS} sec)")
            print("  Press Ctrl+C to stop early.")
            print("=" * 60)
            print()

            # --- Shared queue for mic data ---
            audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

            def audio_callback(indata, frames, time_info, status):
                """Called by sounddevice from a background thread."""
                if status:
                    print(f"  [mic status] {status}", file=sys.stderr)
                # indata is a numpy array of float32 — convert to int16 PCM
                pcm_bytes = b""
                for sample in indata[:, 0]:
                    clamped = max(-1.0, min(1.0, float(sample)))
                    int16_val = int(clamped * 32767)
                    pcm_bytes += struct.pack("<h", int16_val)
                # Use the captured loop reference (thread-safe)
                loop.call_soon_threadsafe(audio_queue.put_nowait, pcm_bytes)

            # --- Start mic stream ---
            mic_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=CHUNK_FRAMES,
                callback=audio_callback,
            )
            mic_stream.start()

            async def send_audio():
                """Read mic chunks from queue and send to backend."""
                total_chunks = int(SAMPLE_RATE / CHUNK_FRAMES * RECORD_SECONDS)
                sent = 0
                while sent < total_chunks:
                    pcm_data = await audio_queue.get()
                    if pcm_data is None:
                        break
                    b64 = base64.b64encode(pcm_data).decode("ascii")
                    await ws.send(json.dumps({
                        "type": "audio_chunk",
                        "audio": b64,
                    }))
                    sent += 1
                # Signal end
                await ws.send(json.dumps({"type": "audio_commit"}))
                # Wait a bit for final transcripts to arrive
                await asyncio.sleep(3)

            async def receive_transcripts():
                """Print transcriptions as they arrive."""
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg["type"] == "transcript_partial":
                            sys.stdout.write(f"\r  [partial] {msg['text']}")
                            sys.stdout.flush()
                        elif msg["type"] == "transcript_final":
                            sys.stdout.write("\r" + " " * 80 + "\r")
                            print(f"  [FINAL]   {msg['text']}")
                        elif msg["type"] == "error":
                            print(f"\n  [ERROR]   {msg['message']}")
                        elif msg["type"] == "session_ready":
                            pass
                        else:
                            print(f"\n  [event]   {msg}")
                except websockets.exceptions.ConnectionClosed:
                    pass

            try:
                await asyncio.gather(send_audio(), receive_transcripts())
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
            finally:
                mic_stream.stop()
                mic_stream.close()

    except ConnectionRefusedError:
        print("ERROR: Cannot connect. Is the backend running?")
        print("  Start it with:  uvicorn app.main:app --reload")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDone.")
