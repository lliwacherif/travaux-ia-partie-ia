from fastapi import APIRouter, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
import tempfile
import os
import json
import asyncio
import base64
import logging
from typing import Any

import websockets

from app.services.ai_service import ai_service
from app.core.config import settings

router = APIRouter(tags=["voice"])
logger = logging.getLogger(__name__)

_BATCH_TRANSCRIPTION_MODEL = "whisper-1"
_SILENCE_SHORT_TRANSCRIPT_WORD_LIMIT = 6
_SILENCE_NO_SPEECH_PROB_THRESHOLD = 0.6
_SILENCE_STRICT_NO_SPEECH_PROB_THRESHOLD = 0.75
_SILENCE_LOW_CONFIDENCE_LOGPROB_THRESHOLD = -0.8


def _get_field(value: Any, field: str, default: Any = None) -> Any:
    """Return ``field`` from a Pydantic model or dict-like object."""
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _extract_transcription_text(transcription: Any) -> str:
    """Normalise OpenAI transcription payloads into a plain string."""
    if isinstance(transcription, str):
        return transcription.strip()

    text = _get_field(transcription, "text", "")
    return text.strip() if isinstance(text, str) else ""


def _should_suppress_silent_transcription(transcription: Any) -> bool:
    """Detect likely silence/noise hallucinations from Whisper segment stats."""
    text = _extract_transcription_text(transcription)
    if not text:
        return True

    segments = _get_field(transcription, "segments", []) or []
    if not isinstance(segments, list) or not segments:
        return False

    if len(text.split()) > _SILENCE_SHORT_TRANSCRIPT_WORD_LIMIT:
        return False

    no_speech_scores: list[float] = []
    low_confidence_segments = 0

    for segment in segments:
        no_speech_prob = _get_field(segment, "no_speech_prob")
        avg_logprob = _get_field(segment, "avg_logprob")

        if isinstance(no_speech_prob, (int, float)):
            no_speech_scores.append(float(no_speech_prob))

        if (
            isinstance(avg_logprob, (int, float))
            and float(avg_logprob) <= _SILENCE_LOW_CONFIDENCE_LOGPROB_THRESHOLD
        ):
            low_confidence_segments += 1

    if not no_speech_scores:
        return False

    avg_no_speech_prob = sum(no_speech_scores) / len(no_speech_scores)
    short_transcript = len(text.split()) <= 3
    all_segments_low_confidence = low_confidence_segments == len(segments)

    if short_transcript and avg_no_speech_prob >= _SILENCE_NO_SPEECH_PROB_THRESHOLD:
        return True

    return (
        avg_no_speech_prob >= _SILENCE_STRICT_NO_SPEECH_PROB_THRESHOLD
        and all_segments_low_confidence
    )

# ---------------------------------------------------------------------------
# 1. Batch transcription (existing endpoint — unchanged)
# ---------------------------------------------------------------------------

@router.post("/voice", summary="Transcribe audio to text")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribes an uploaded audio file to text using OpenAI's Whisper model."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    suffix = ""
    if "." in file.filename:
        suffix = f".{file.filename.split('.')[-1]}"

    content = await file.read()
    if not content:
        return {"text": ""}

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as audio_file:
            transcription = await ai_service._client.audio.transcriptions.create(
                model=_BATCH_TRANSCRIPTION_MODEL,
                file=audio_file,
                response_format="verbose_json",
                temperature=0,
                timestamp_granularities=["segment"],
            )

        text = _extract_transcription_text(transcription)
        if _should_suppress_silent_transcription(transcription):
            logger.info(
                "Suppressed probable no-speech transcription for uploaded file '%s'.",
                file.filename,
            )
            text = ""

        return {"text": text}
    except Exception as e:
        logger.exception("Voice transcription failed.")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 2. Real-time streaming transcription via OpenAI Realtime API
# ---------------------------------------------------------------------------

_OPENAI_REALTIME_TRANSCRIPTION_MODEL = "gpt-realtime-whisper"
_OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime"
    f"?model={_OPENAI_REALTIME_TRANSCRIPTION_MODEL}"
)

# Session configuration sent to OpenAI once the connection opens.
# Keep this as a transcription-only session. A regular realtime session can
# generate assistant responses; this endpoint must only return speech-to-text.
_SESSION_CONFIG = {
    "type": "session.update",
    "session": {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {
                    "type": "audio/pcm",
                    "rate": 24000,
                },
                "transcription": {
                    "model": _OPENAI_REALTIME_TRANSCRIPTION_MODEL,
                    "language": "fr",
                    "delay": "low",
                },
                "turn_detection": None,
            }
        },
    },
}


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket):
    """Real-time audio transcription via WebSocket.

    **Protocol (frontend ↔ backend)**

    1. Frontend opens a WebSocket to ``/api/v1/voice/stream``.
    2. Frontend sends JSON frames::

           {"type": "audio_chunk", "audio": "<base64-encoded PCM16 24kHz mono>"}

       To signal the end of an utterance and request transcription::

           {"type": "audio_commit"}

    3. Backend streams back JSON frames::

           {"type": "transcript_partial", "text": "Bonjour je voud..."}
           {"type": "transcript_final",   "text": "Bonjour je voudrais un devis."}
           {"type": "error",              "message": "..."}
           {"type": "session_ready"}

    4. Either side can close the connection normally.
    """
    await ws.accept()
    logger.info("Voice stream WebSocket connected from client.")

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        await ws.send_json({"type": "error", "message": "Server has no OpenAI API key configured."})
        await ws.close(code=1008, reason="Missing API key")
        return

    openai_ws = None
    try:
        # --- Open upstream connection to OpenAI Realtime API ---------------
        openai_ws = await websockets.connect(
            _OPENAI_REALTIME_URL,
            additional_headers={
                "Authorization": f"Bearer {api_key}",
            },
        )
        logger.info("Connected to OpenAI Realtime API.")

        # Send session configuration
        await openai_ws.send(json.dumps(_SESSION_CONFIG))
        logger.debug("Sent session.update to OpenAI.")

        # ------------------------------------------------------------------
        # Two concurrent tasks: relay audio up, relay transcripts down.
        # ------------------------------------------------------------------

        async def _relay_frontend_to_openai():
            """Read audio chunks from the frontend and forward to OpenAI."""
            has_pending_audio = False
            try:
                while True:
                    raw = await ws.receive_text()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "Invalid JSON received."})
                        continue

                    msg_type = msg.get("type", "")

                    if msg_type == "audio_chunk":
                        audio_b64 = msg.get("audio", "")
                        if not audio_b64:
                            continue
                        # Validate that the payload is valid base64
                        try:
                            base64.b64decode(audio_b64, validate=True)
                        except Exception:
                            await ws.send_json({"type": "error", "message": "Invalid base64 audio data."})
                            continue
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": audio_b64,
                        }))
                        has_pending_audio = True

                    elif msg_type == "audio_commit":
                        # Frontend explicitly ends an utterance.
                        if has_pending_audio:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.commit",
                            }))
                            has_pending_audio = False

                    elif msg_type == "ping":
                        await ws.send_json({"type": "pong"})

                    else:
                        logger.debug("Unknown message type from frontend: %s", msg_type)

            except WebSocketDisconnect:
                logger.info("Frontend WebSocket disconnected.")
            except Exception as exc:
                logger.exception("Error in frontend->OpenAI relay: %s", exc)

        async def _relay_openai_to_frontend():
            """Read events from OpenAI and forward transcripts to the frontend."""
            session_ready_sent = False
            try:
                async for raw_event in openai_ws:
                    try:
                        event = json.loads(raw_event)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Session is configured and ready.
                    if event_type in (
                        "session.updated",
                        "transcription_session.updated",
                    ) and not session_ready_sent:
                        await ws.send_json({"type": "session_ready"})
                        session_ready_sent = True

                    # Partial transcript (real-time updating text)
                    elif event_type == "conversation.item.input_audio_transcription.delta":
                        delta = event.get("delta", "")
                        if delta:
                            await ws.send_json({
                                "type": "transcript_partial",
                                "text": delta,
                            })

                    # Final transcript (completed sentence)
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "")
                        await ws.send_json({
                            "type": "transcript_final",
                            "text": transcript,
                        })

                    # OpenAI-side errors
                    elif event_type == "error":
                        error_body = event.get("error", {})
                        error_msg = error_body.get("message", str(error_body))
                        logger.warning("OpenAI Realtime error: %s", error_msg)
                        await ws.send_json({
                            "type": "error",
                            "message": f"OpenAI: {error_msg}",
                        })

                    # Log other events at debug level for troubleshooting
                    else:
                        logger.debug("OpenAI event (unhandled): %s", event_type)

            except websockets.exceptions.ConnectionClosed as exc:
                logger.info("OpenAI WebSocket closed: %s", exc)
            except Exception as exc:
                logger.exception("Error in OpenAI->frontend relay: %s", exc)

        # Run both relay loops concurrently; when either exits, cancel the other.
        relay_tasks = [
            asyncio.create_task(_relay_frontend_to_openai(), name="frontend->openai"),
            asyncio.create_task(_relay_openai_to_frontend(), name="openai->frontend"),
        ]
        done, pending = await asyncio.wait(relay_tasks, return_when=asyncio.FIRST_COMPLETED)

        # Cancel whichever task is still running
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    except websockets.exceptions.InvalidStatusCode as exc:
        logger.error("Failed to connect to OpenAI Realtime API: HTTP %s", exc.status_code)
        try:
            await ws.send_json({
                "type": "error",
                "message": f"Failed to connect to OpenAI (HTTP {exc.status_code}). Check API key and permissions.",
            })
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Voice stream unexpected error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": f"Server error: {exc}"})
        except Exception:
            pass
    finally:
        # Clean shutdown of both connections
        if openai_ws is not None:
            try:
                await openai_ws.close()
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("Voice stream WebSocket session ended.")

