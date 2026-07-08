# Real-Time Voice Transcription – Frontend Integration Guide

> **Endpoint:** `ws://<backend-host>/api/v1/voice/stream`
>
> **Protocol:** WebSocket (JSON frames over text messages)
>
> **Audio format:** PCM16, 24 kHz, mono, Base64-encoded

---

## Overview

This WebSocket endpoint enables **real-time, word-by-word speech-to-text**.
The frontend captures microphone audio, encodes it, and streams it to the
backend.  The backend securely relays the audio to OpenAI's Realtime API and
forwards transcription events back to the frontend.

This endpoint is transcription-only. It does not return assistant messages,
spoken replies, or chat completions. If the UI shows an AI answer after using
this socket, the frontend is either connected to the wrong voice-agent API or
is forwarding `transcript_final` into a chatbot/devis endpoint after the
transcription completes.

```
┌──────────┐   WebSocket    ┌──────────┐   WebSocket    ┌──────────┐
│ Frontend │ ────────────►  │ Backend  │ ────────────►  │ OpenAI   │
│ (mic)    │ ◄────────────  │ (proxy)  │ ◄────────────  │ Realtime │
└──────────┘  transcripts   └──────────┘  transcripts   └──────────┘
```

---

## 1. Connection

Open a standard WebSocket connection:

```javascript
const ws = new WebSocket("ws://localhost:8000/api/v1/voice/stream");
// In production, use wss:// with your domain
```

### Events

| Frontend Event | Description                        |
|----------------|------------------------------------|
| `onopen`       | Connection established — start mic |
| `onmessage`    | Transcription data received        |
| `onclose`      | Session ended                      |
| `onerror`      | Connection error                   |

Wait for the **`session_ready`** message before sending audio:

```javascript
ws.onopen = () => {
    console.log("WebSocket connected, waiting for session_ready...");
};
```

---

## 2. Capturing Microphone Audio (PCM16, 24 kHz)

Use the Web Audio API to capture the microphone and convert to the required
PCM16 format.

```javascript
let audioContext;
let processor;
let source;

async function startMicrophone(ws) {
    const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
            sampleRate: 24000,
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
        }
    });

    audioContext = new AudioContext({ sampleRate: 24000 });
    source = audioContext.createMediaStreamSource(stream);

    // ScriptProcessorNode (or AudioWorkletNode for modern approach)
    // Buffer size of 4096 gives ~170ms chunks at 24kHz
    processor = audioContext.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (event) => {
        const float32 = event.inputBuffer.getChannelData(0);
        const pcm16 = float32ToPcm16(float32);
        const base64Audio = arrayBufferToBase64(pcm16.buffer);

        ws.send(JSON.stringify({
            type: "audio_chunk",
            audio: base64Audio,
        }));
    };

    source.connect(processor);
    processor.connect(audioContext.destination);
}

// Convert Float32 samples [-1.0, 1.0] to Int16 PCM
function float32ToPcm16(float32Array) {
    const int16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
        const s = Math.max(-1, Math.min(1, float32Array[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return int16;
}

// Convert ArrayBuffer to Base64 string
function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}
```

---

## 3. Sending Audio to the Backend

### Message types the frontend sends:

| Type            | Payload                              | When                            |
|-----------------|--------------------------------------|---------------------------------|
| `audio_chunk`   | `{ "type": "audio_chunk", "audio": "<base64>" }` | Continuously while recording    |
| `audio_commit`  | `{ "type": "audio_commit" }`         | End the current utterance and request transcription |
| `ping`          | `{ "type": "ping" }`                | Keep-alive / latency check      |

> **Note:** Send `audio_commit` when the user pauses, releases push-to-talk, or
> taps stop. The backend uses OpenAI's `gpt-realtime-whisper` in a
> transcription-only session; for this model the OpenAI Realtime transcription
> docs say to omit/null turn detection and commit audio manually. For hands-free
> UX, implement client-side silence detection and send `audio_commit` at pause
> boundaries.

---

## 4. Receiving Transcriptions

### Message types the frontend receives:

| Type                  | Payload                                            | Description                          |
|-----------------------|----------------------------------------------------|--------------------------------------|
| `session_ready`       | `{ "type": "session_ready" }`                      | Backend is ready — start sending     |
| `transcript_partial`  | `{ "type": "transcript_partial", "text": "..." }`  | Live, updating text (display in grey)|
| `transcript_final`    | `{ "type": "transcript_final", "text": "..." }`    | Completed sentence (display final)   |
| `error`               | `{ "type": "error", "message": "..." }`            | Error from backend or OpenAI         |
| `pong`                | `{ "type": "pong" }`                               | Response to a ping                   |

### Example handler:

```javascript
let partialText = "";
let finalText = "";

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
        case "session_ready":
            console.log("Session ready! Starting microphone...");
            startMicrophone(ws);
            break;

        case "transcript_partial":
            // Accumulate partial deltas for live preview
            partialText += data.text;
            updateUI(finalText + partialText);  // Show grey "typing" text
            break;

        case "transcript_final":
            // Replace partial with the finalized sentence
            finalText += data.text + " ";
            partialText = "";
            updateUI(finalText);  // Show solid final text
            break;

        case "error":
            console.error("Transcription error:", data.message);
            break;
    }
};

function updateUI(text) {
    document.getElementById("transcription-output").textContent = text;
}
```

---

## 5. Stopping Recording

```javascript
async function stopRecording() {
    // 1. Stop the audio processor
    if (processor) {
        processor.disconnect();
        processor = null;
    }
    if (source) {
        source.disconnect();
        source = null;
    }
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }

    // 2. Commit the buffered audio before closing so the final transcript arrives
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "audio_commit" }));
        await new Promise((resolve) => setTimeout(resolve, 1500));
        ws.close();
    }
}
```

---

## 6. Complete Minimal Example

```html
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Real-Time Transcription</title>
</head>
<body>
    <h1>🎙️ Transcription en temps réel</h1>
    <button id="startBtn">Démarrer</button>
    <button id="stopBtn" disabled>Arrêter</button>
    <div id="output" style="
        margin-top: 20px;
        padding: 15px;
        border: 1px solid #ccc;
        min-height: 100px;
        font-size: 18px;
        white-space: pre-wrap;
    "></div>

    <script>
        let ws, audioContext, processor, source;
        let partialText = "", finalText = "";
        let shouldCloseAfterFinal = false;
        let closeTimer = null;

        document.getElementById("startBtn").onclick = () => {
            ws = new WebSocket("ws://localhost:8000/api/v1/voice/stream");

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === "session_ready") {
                    startMic();
                    document.getElementById("startBtn").disabled = true;
                    document.getElementById("stopBtn").disabled = false;
                } else if (data.type === "transcript_partial") {
                    partialText += data.text;
                    document.getElementById("output").textContent = finalText + partialText;
                } else if (data.type === "transcript_final") {
                    finalText += data.text + " ";
                    partialText = "";
                    document.getElementById("output").textContent = finalText;
                    if (shouldCloseAfterFinal && ws.readyState === WebSocket.OPEN) {
                        clearTimeout(closeTimer);
                        ws.close();
                    }
                } else if (data.type === "error") {
                    console.error("Error:", data.message);
                }
            };

            ws.onclose = () => { stopMic(); };
        };

        document.getElementById("stopBtn").onclick = () => {
            stopMic();
            shouldCloseAfterFinal = true;
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "audio_commit" }));
                closeTimer = setTimeout(() => {
                    if (ws.readyState === WebSocket.OPEN) ws.close();
                }, 1500);
            }
            document.getElementById("startBtn").disabled = false;
            document.getElementById("stopBtn").disabled = true;
        };

        async function startMic() {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 24000, channelCount: 1 }
            });
            audioContext = new AudioContext({ sampleRate: 24000 });
            source = audioContext.createMediaStreamSource(stream);
            processor = audioContext.createScriptProcessor(4096, 1, 1);
            processor.onaudioprocess = (e) => {
                const f32 = e.inputBuffer.getChannelData(0);
                const i16 = new Int16Array(f32.length);
                for (let i = 0; i < f32.length; i++) {
                    const s = Math.max(-1, Math.min(1, f32[i]));
                    i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                const bytes = new Uint8Array(i16.buffer);
                let bin = "";
                for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                ws.send(JSON.stringify({ type: "audio_chunk", audio: btoa(bin) }));
            };
            source.connect(processor);
            processor.connect(audioContext.destination);
        }

        function stopMic() {
            if (processor) { processor.disconnect(); processor = null; }
            if (source) { source.disconnect(); source = null; }
            if (audioContext) { audioContext.close(); audioContext = null; }
        }
    </script>
</body>
</html>
```

---

## 7. Error Handling Checklist

| Scenario                   | What happens                                        |
|----------------------------|-----------------------------------------------------|
| Backend has no API key     | `{ "type": "error", "message": "Server has no..." }` → WebSocket closes |
| OpenAI rejects the key     | `{ "type": "error", "message": "Failed to connect to OpenAI (HTTP 401)..." }` |
| User denies mic permission | Handle in frontend `getUserMedia` catch block        |
| Network drops              | `ws.onclose` fires — reconnect or show UI message    |
| OpenAI rate limit          | `{ "type": "error", "message": "OpenAI: ..." }`     |

---

## 8. Important Notes

- **Audio format is strict**: OpenAI expects **PCM16, 24 kHz, mono**.
  Any other sample rate or encoding will cause errors or garbled output.
- **Manual commits are required**: The backend uses a transcription-only
  `gpt-realtime-whisper` session with turn detection disabled. Send
  `audio_commit` when an utterance should be transcribed.
- **No assistant response is produced by this socket**: Treat
  `transcript_final` as text only. Do not pass it into a chat/devis endpoint
  unless the user explicitly submits the transcript for an AI response.
- **The existing `/voice` endpoint still works**: For one-shot file uploads
  (e.g., sending a pre-recorded audio file), continue using `POST /api/v1/voice`.
- **CORS / WebSocket origin**: The current CORS config allows all origins.
  For production, restrict `allow_origin_regex` in `main.py`.
