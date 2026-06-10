from fastapi import APIRouter, UploadFile, File, HTTPException
import tempfile
import os
import logging
from app.services.ai_service import ai_service

router = APIRouter(tags=["voice"])
logger = logging.getLogger(__name__)

@router.post("/voice", summary="Transcribe audio to text")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribes an uploaded audio file to text using OpenAI's Whisper model."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    
    suffix = ""
    if "." in file.filename:
        suffix = f".{file.filename.split('.')[-1]}"
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as audio_file:
            transcription = await ai_service._client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )
        return {"text": transcription}
    except Exception as e:
        logger.exception("Voice transcription failed.")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
