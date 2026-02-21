"""
voice.py — Voice routes (แปลงจาก voice.ts)
ใช้ FastAPI APIRouter แทน Elysia plugin
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from firebase_admin import firestore
from pydantic import BaseModel, Field
from typing import Optional
import httpx

STT_URL = "http://localhost:5001"

router = APIRouter()

# ── DB lazy ────────────────────────────────────────────
_db = None
def get_db():
    global _db
    if _db is None:
        _db = firestore.client()
    return _db

def messages_col():
    return get_db().collection("messages")

def rooms_col():
    return get_db().collection("rooms")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ══════════════════════════════════════════════════════
# 1. ตรวจสอบสถานะ STT Server
# ══════════════════════════════════════════════════════
@router.get("/stt/health")
async def stt_health():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            res = await client.get(f"{STT_URL}/health")
        return {"online": True, **res.json()}
    except Exception as e:
        return {"online": False, "error": "STT server ไม่ตอบสนอง"}

# ══════════════════════════════════════════════════════
# 2. ถอดเสียงจากไฟล์เสียง
# POST /rooms/{roomId}/transcribe
# ══════════════════════════════════════════════════════
@router.post("/rooms/{room_id}/transcribe")
async def transcribe(
    room_id: str,
    audio: UploadFile = File(...),
    lang: str = Form("th-TH"),
):
    room_snap = rooms_col().document(room_id).get()
    if not room_snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")

    audio_bytes = await audio.read()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(
                f"{STT_URL}/transcribe",
                files={"audio": (audio.filename, audio_bytes, audio.content_type)},
                data={"lang": lang},
            )
        return res.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"STT server ไม่ตอบสนอง: {e}")

# ══════════════════════════════════════════════════════
# 3. บันทึก transcript เป็นข้อความในห้อง
# POST /rooms/{roomId}/voice-message
# ══════════════════════════════════════════════════════
class VoiceMessageBody(BaseModel):
    text: str = Field(..., min_length=1)
    displayName: str = Field(..., min_length=1)
    uid: str
    photoURL: Optional[str] = None
    duration_ms: Optional[float] = None

@router.post("/rooms/{room_id}/voice-message")
async def voice_message(room_id: str, body: VoiceMessageBody):
    room_snap = rooms_col().document(room_id).get()
    if not room_snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")

    duration_fmt = f" *({round((body.duration_ms or 0) / 1000)}s)*" if body.duration_ms else ""
    msg_text = f"🎙️ **Voice** {duration_fmt}\n{body.text}"

    doc_ref = messages_col().document()
    doc_ref.set({
        "roomId":      room_id,
        "uid":         body.uid,
        "username":    body.displayName,
        "photoURL":    body.photoURL or "",
        "text":        msg_text,
        "isVoice":     True,
        "transcript":  body.text,
        "duration_ms": body.duration_ms or 0,
        "edited":      False,
        "replyTo":     None,
        "createdAt":   firestore.SERVER_TIMESTAMP,
        "updatedAt":   firestore.SERVER_TIMESTAMP,
    })

    message = {
        "id":          doc_ref.id,
        "roomId":      room_id,
        "uid":         body.uid,
        "username":    body.displayName,
        "photoURL":    body.photoURL or "",
        "text":        msg_text,
        "isVoice":     True,
        "transcript":  body.text,
        "duration_ms": body.duration_ms or 0,
        "edited":      False,
        "replyTo":     None,
        "createdAt":   now_iso(),
    }

    return {"success": True, "message": message}