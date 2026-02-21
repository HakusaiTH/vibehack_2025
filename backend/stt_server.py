#!/usr/bin/env python3
"""
stt_server.py — STT Server สำหรับ Elysian Chat
รับไฟล์เสียง (webm/wav/mp3) แล้วถอดเสียงเป็นข้อความภาษาไทย
แปลงจาก HTTPServer แบบ raw → FastAPI
"""

import io
import os
import tempfile
import traceback

import speech_recognition as sr
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydub import AudioSegment

app = FastAPI(title="Elysian STT Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# ══════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {"status": "ok", "service": "elysian-stt"}

# ══════════════════════════════════════════════════════
# TRANSCRIBE
# ══════════════════════════════════════════════════════
@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    lang: str = Form("th-TH"),
):
    audio_data = await audio.read()
    filename   = audio.filename or "audio.webm"

    print(f"[STT] รับไฟล์: {filename} ({len(audio_data)} bytes) lang={lang}")

    if len(audio_data) < 100:
        return JSONResponse(
            status_code=400,
            content={"error": "ไฟล์เสียงว่างเปล่าหรือเสียหาย"},
        )

    _, ext = os.path.splitext(filename)
    ext = ext.lower() or ".webm"

    tmp_in  = tempfile.NamedTemporaryFile(suffix=ext,  delete=False)
    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_in.write(audio_data)
    tmp_in.close()
    tmp_wav.close()

    try:
        print(f"[STT] แปลง {ext} → WAV ...")
        if ext in (".webm", ".ogg", ".opus"):
            sound = AudioSegment.from_file(tmp_in.name, format="webm")
        elif ext == ".mp3":
            sound = AudioSegment.from_mp3(tmp_in.name)
        elif ext == ".wav":
            sound = AudioSegment.from_wav(tmp_in.name)
        elif ext == ".m4a":
            sound = AudioSegment.from_file(tmp_in.name, format="m4a")
        else:
            sound = AudioSegment.from_file(tmp_in.name)

        # Mono 16kHz — ประหยัด bandwidth และแม่นขึ้น
        sound = sound.set_channels(1).set_frame_rate(16000)
        sound.export(tmp_wav.name, format="wav")
        duration_ms = len(sound)
        print(f"[STT] แปลงสำเร็จ: {duration_ms}ms")

        r = sr.Recognizer()
        r.energy_threshold = 300
        r.dynamic_energy_threshold = True

        with sr.AudioFile(tmp_wav.name) as source:
            r.adjust_for_ambient_noise(source, duration=min(0.5, duration_ms / 2000))
            audio_rec = r.record(source)

        print(f"[STT] ส่ง Google STT (lang={lang}) ...")
        text = r.recognize_google(audio_rec, language=lang)
        print(f"[STT] ✅ ผลลัพธ์: {text}")

        return {
            "success": True,
            "text": text,
            "lang": lang,
            "duration_ms": duration_ms,
        }

    except sr.UnknownValueError:
        print("[STT] ⚠️ ถอดเสียงไม่ได้")
        return {
            "success": False,
            "text": "",
            "error": "ไม่สามารถถอดเสียงได้ อาจเงียบเกินไปหรือพูดไม่ชัด",
        }
    except sr.RequestError as e:
        print(f"[STT] ❌ Google API error: {e}")
        return JSONResponse(status_code=503, content={"error": f"Google STT ไม่ตอบสนอง: {e}"})
    except Exception as e:
        print(f"[STT] ❌ Error: {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": f"ถอดเสียงไม่สำเร็จ: {e}"})
    finally:
        try:
            os.unlink(tmp_in.name)
            if os.path.exists(tmp_wav.name):
                os.unlink(tmp_wav.name)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    print("""
╔══════════════════════════════════════════╗
║   🎙️  Elysian STT Server (FastAPI)     ║
║   Port: 5001                             ║
║   POST /transcribe  - ถอดเสียงเป็นข้อความ  ║
║   GET  /health      - ตรวจสอบสถานะ       ║
╚══════════════════════════════════════════╝
""")
    uvicorn.run("stt_server:app", host="0.0.0.0", port=5001, reload=False)