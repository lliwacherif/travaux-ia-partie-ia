# Voice Transcription Endpoint

**Endpoint:** `POST /api/v1/voice`  
**Description:** Receives an audio file (recorded from the user's microphone) and returns the transcribed text using the configured OpenAI speech-to-text model (default: `gpt-4o-transcribe`).  
**Authentication:** None required.  

### Request format
- **Method:** `POST`
- **Content-Type:** `multipart/form-data`
- **Body Parameters:**
  - `file` (File): The audio file to be transcribed. Supported formats include `mp3`, `mp4`, `mpeg`, `mpga`, `m4a`, `wav`, and `webm` (max size: 25 MB).

### Response format (JSON)
Returns the transcribed text as a string inside a JSON object.
```json
{
  "text": "The transcribed string will appear here."
}
```

---

### Example 1: cURL

```bash
curl -X POST http://localhost:8000/api/v1/voice \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@/path/to/your/audio_recording.webm"
```

### Example 2: JavaScript (Frontend Fetch)

```javascript
// 1. Get the audio recording blob from the MediaRecorder API
// const audioBlob = ...;

// 2. Wrap it in a FormData object
const formData = new FormData();
// Important: Ensure you append it with the name "file"
formData.append("file", audioBlob, "recording.webm");

try {
  // 3. Send the POST request to the API
  const response = await fetch("http://localhost:8000/api/v1/voice", {
    method: "POST",
    body: formData
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  // 4. Retrieve the transcribed text
  const data = await response.json();
  console.log("Transcribed Text:", data.text);
  
} catch (error) {
  console.error("Transcription failed:", error);
}
```
