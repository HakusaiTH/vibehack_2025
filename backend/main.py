"""
Elysian Chat — FastAPI Backend
แปลงจาก Elysia (TypeScript) → FastAPI (Python)
"""

import asyncio
import base64
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional, Set

import httpx
import firebase_admin
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from firebase_admin import auth, credentials, firestore
from pydantic import BaseModel, Field

from rag import ragQuery, save_message_embedding, save_pdf_embeddings
from voice import router as voice_router

# ══════════════════════════════════════════════════════
# FIREBASE INIT
# ══════════════════════════════════════════════════════
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

db = firestore.client()
messages_ref  = db.collection("messages")
rooms_ref     = db.collection("rooms")
users_ref     = db.collection("users")
files_ref     = db.collection("files")
embeddings_ref = db.collection("embeddings")

LM = "http://localhost:1234/v1"

# ══════════════════════════════════════════════════════
# WEBSOCKET BROADCAST
# ══════════════════════════════════════════════════════
clients: Set[WebSocket] = set()

async def broadcast(data: dict):
    payload = json.dumps(data, ensure_ascii=False)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)

# ══════════════════════════════════════════════════════
# AUTH DEPENDENCY
# ══════════════════════════════════════════════════════
async def get_current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: no token")

    token = header[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: empty token")

    try:
        # check_revoked=False เพื่อหลีกเลี่ยง network call พิเศษ
        decoded = auth.verify_id_token(token, check_revoked=False)
        return decoded
    except auth.ExpiredIdTokenError:
        print("⚠️  Token expired")
        raise HTTPException(status_code=401, detail="Unauthorized: token expired")
    except auth.InvalidIdTokenError as e:
        print(f"⚠️  Invalid token: {e}")
        raise HTTPException(status_code=401, detail=f"Unauthorized: invalid token")
    except auth.CertificateFetchError as e:
        print(f"⚠️  Certificate fetch error: {e}")
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    except Exception as e:
        # แสดง error จริงๆ เพื่อ debug
        print(f"❌  verify_id_token unexpected error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail=f"Unauthorized: {type(e).__name__}")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def doc_to_dict(doc) -> dict:
    data = doc.to_dict()
    data["id"] = doc.id
    for k, v in data.items():
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
    return data

# ══════════════════════════════════════════════════════
# PYDANTIC SCHEMAS
# ══════════════════════════════════════════════════════
class ReplyTo(BaseModel):
    id: str
    username: str
    text: str
    fileId: Optional[str] = None
    filename: Optional[str] = None
    isPdfMsg: Optional[bool] = None

class CreateRoomBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    description: Optional[str] = Field(None, max_length=120)
    emoji: Optional[str] = "💬"
    isPrivate: Optional[bool] = False

class UpdateRoomBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=32)
    description: Optional[str] = Field(None, max_length=120)
    emoji: Optional[str] = None
    isPrivate: Optional[bool] = None

class SendMessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    displayName: str = Field(..., min_length=1, max_length=32)
    photoURL: Optional[str] = None
    replyTo: Optional[ReplyTo] = None

class EditMessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)

class BaikaoBody(BaseModel):
    question: str = Field(..., min_length=1)
    displayName: str = Field(..., min_length=1)
    replyTo: Optional[ReplyTo] = None

# ══════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════
app = FastAPI(title="Elysian Chat API", version="5.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(voice_router)

# ══════════════════════════════════════════════════════
# ROOT
# ══════════════════════════════════════════════════════
@app.get("/")
def root():
    return {"status": "ok", "version": "5.2"}

# ══════════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════════
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps({"type": "connected", "clientCount": len(clients)}))
    print(f"🟢 WS | clients: {len(clients)}")
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        clients.discard(ws)
        print(f"🔴 WS | clients: {len(clients)}")

# ══════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════
@app.post("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    uid = user["uid"]
    ref = users_ref.document(uid)
    snap = ref.get()
    profile = {
        "uid": uid,
        "email": user.get("email", ""),
        "displayName": user.get("name") or (user.get("email", "").split("@")[0]) or "User",
        "photoURL": user.get("picture", ""),
        "lastSeen": firestore.SERVER_TIMESTAMP,
    }
    if not snap.exists:
        ref.set({**profile, "createdAt": firestore.SERVER_TIMESTAMP})
    else:
        ref.update({"lastSeen": profile["lastSeen"], "displayName": profile["displayName"]})
    data = ref.get().to_dict()
    return {"uid": data["uid"], "email": data["email"], "displayName": data["displayName"], "photoURL": data["photoURL"]}

# ══════════════════════════════════════════════════════
# ROOMS
# ══════════════════════════════════════════════════════
@app.get("/rooms")
async def get_rooms(user: dict = Depends(get_current_user)):
    snap = rooms_ref.order_by("createdAt", direction=firestore.Query.ASCENDING).stream()
    rooms = []
    for d in snap:
        data = d.to_dict()
        data["id"] = d.id
        if hasattr(data.get("createdAt"), "isoformat"):
            data["createdAt"] = data["createdAt"].isoformat()
        rooms.append(data)
    return {"rooms": rooms}

@app.post("/rooms", status_code=201)
async def create_room(body: CreateRoomBody, user: dict = Depends(get_current_user)):
    existing = rooms_ref.where("name", "==", body.name).get()
    if existing:
        raise HTTPException(status_code=409, detail="Room name already exists")
    doc_ref = rooms_ref.document()
    room_data = {
        "name": body.name,
        "description": body.description or "",
        "emoji": body.emoji or "💬",
        "isPrivate": body.isPrivate or False,
        "createdBy": user["uid"],
        "createdByName": user.get("name") or user.get("email") or "Unknown",
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    doc_ref.set(room_data)
    room = {
        "id": doc_ref.id, "name": body.name,
        "description": body.description or "", "emoji": body.emoji or "💬",
        "isPrivate": body.isPrivate or False, "createdBy": user["uid"],
        "createdByName": user.get("name") or user.get("email") or "Unknown",
        "createdAt": now_iso(),
    }
    await broadcast({"type": "room_created", "room": room})
    return {"success": True, "room": room}

@app.patch("/rooms/{room_id}")
async def update_room(room_id: str, body: UpdateRoomBody, user: dict = Depends(get_current_user)):
    doc_ref = rooms_ref.document(room_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")
    if snap.to_dict().get("createdBy") != user["uid"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    updates: dict[str, Any] = {"updatedAt": firestore.SERVER_TIMESTAMP}
    if body.name        is not None: updates["name"]        = body.name
    if body.description is not None: updates["description"] = body.description
    if body.emoji       is not None: updates["emoji"]       = body.emoji
    if body.isPrivate   is not None: updates["isPrivate"]   = body.isPrivate
    doc_ref.update(updates)
    updated = {**snap.to_dict(), "id": room_id, **updates, "updatedAt": now_iso()}
    await broadcast({"type": "room_updated", "room": updated})
    return {"success": True, "room": updated}

@app.delete("/rooms/{room_id}")
async def delete_room(room_id: str, user: dict = Depends(get_current_user)):
    doc_ref = rooms_ref.document(room_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")
    if snap.to_dict().get("createdBy") != user["uid"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    msg_snap  = messages_ref.where("roomId", "==", room_id).stream()
    emb_snap  = embeddings_ref.where("roomId", "==", room_id).stream()
    file_snap = files_ref.where("roomId", "==", room_id).stream()
    batch = db.batch()
    for d in msg_snap:  batch.delete(d.reference)
    for d in emb_snap:  batch.delete(d.reference)
    for d in file_snap: batch.delete(d.reference)
    batch.delete(doc_ref)
    batch.commit()
    await broadcast({"type": "room_deleted", "roomId": room_id})
    return {"success": True}

# ══════════════════════════════════════════════════════
# MESSAGES
# ══════════════════════════════════════════════════════
@app.get("/rooms/{room_id}/messages")
async def get_messages(
    room_id: str,
    limit: int = 50,
    before: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    limit = min(limit, 100)
    snap = messages_ref.where("roomId", "==", room_id).stream()
    msgs = []
    for d in snap:
        data = d.to_dict()
        data["id"] = d.id
        for k in ("createdAt", "updatedAt"):
            if hasattr(data.get(k), "isoformat"):
                data[k] = data[k].isoformat()
        msgs.append(data)
    msgs.sort(key=lambda m: m.get("createdAt") or "")
    if before:
        idx = next((i for i, m in enumerate(msgs) if m["id"] == before), None)
        if idx and idx > 0:
            msgs = msgs[:idx]
    has_more = len(msgs) > limit
    return {"messages": msgs[-limit:], "hasMore": has_more}

@app.post("/rooms/{room_id}/messages", status_code=201)
async def send_message(room_id: str, body: SendMessageBody, user: dict = Depends(get_current_user)):
    room_snap = rooms_ref.document(room_id).get()
    if not room_snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")
    doc_ref = messages_ref.document()
    doc_ref.set({
        "roomId": room_id, "uid": user["uid"],
        "username": body.displayName, "photoURL": body.photoURL or "",
        "text": body.text, "edited": False,
        "replyTo": body.replyTo.model_dump() if body.replyTo else None,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    })
    message = {
        "id": doc_ref.id, "roomId": room_id, "uid": user["uid"],
        "username": body.displayName, "photoURL": body.photoURL or "",
        "text": body.text, "edited": False,
        "replyTo": body.replyTo.model_dump() if body.replyTo else None,
        "createdAt": now_iso(), "updatedAt": now_iso(),
    }
    await broadcast({"type": "new_message", "message": message})
    asyncio.create_task(save_message_embedding({
        "id": doc_ref.id, "roomId": room_id, "uid": user["uid"],
        "username": body.displayName, "text": body.text, "createdAt": now_iso(),
    }))
    return {"success": True, "message": message}

@app.patch("/messages/{msg_id}")
async def edit_message(msg_id: str, body: EditMessageBody, user: dict = Depends(get_current_user)):
    doc_ref = messages_ref.document(msg_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Message not found")
    if snap.to_dict().get("uid") != user["uid"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    doc_ref.update({"text": body.text, "edited": True, "updatedAt": firestore.SERVER_TIMESTAMP})
    updated = {**snap.to_dict(), "id": msg_id, "text": body.text, "edited": True, "updatedAt": now_iso()}
    await broadcast({"type": "edit_message", "message": updated})
    return {"success": True, "message": updated}

@app.delete("/messages/{msg_id}")
async def delete_message(msg_id: str, user: dict = Depends(get_current_user)):
    doc_ref = messages_ref.document(msg_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Message not found")
    if snap.to_dict().get("uid") != user["uid"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    room_id = snap.to_dict().get("roomId")
    doc_ref.delete()
    await broadcast({"type": "delete_message", "id": msg_id, "roomId": room_id})
    return {"success": True}

# ══════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════
@app.post("/rooms/{room_id}/upload-pdf", status_code=201)
async def upload_pdf(
    room_id: str,
    file: UploadFile = File(...),
    displayName: str = Form("User"),
    user: dict = Depends(get_current_user),
):
    room_snap = rooms_ref.document(room_id).get()
    if not room_snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    # Extract text with Typhoon OCR API
    pdf_text = ""
    try:
        import io
        import json as _json
        TYPHOON_API_KEY = "sk-eUnBiTXsKNZUR2Jof4mrDYjGFugANia9oy23moQEaQrgye2g"
        async with httpx.AsyncClient(timeout=120) as ocr_client:
            res = await ocr_client.post(
                "https://api.opentyphoon.ai/v1/ocr",
                headers={"Authorization": f"Bearer {TYPHOON_API_KEY}"},
                files={"file": (file.filename, io.BytesIO(content), "application/pdf")},
                data={
                    "model": "typhoon-ocr",
                    "task_type": "default",
                    "max_tokens": "16384",
                    "temperature": "0.1",
                    "top_p": "0.6",
                    "repetition_penalty": "1.2",
                },
            )
        if res.status_code == 200:
            ocr_result = res.json()
            extracted_texts = []
            for page_result in ocr_result.get("results", []):
                if page_result.get("success") and page_result.get("message"):
                    raw_content = page_result["message"]["choices"][0]["message"]["content"]
                    try:
                        parsed = _json.loads(raw_content)
                        text_chunk = parsed.get("natural_text", raw_content)
                    except _json.JSONDecodeError:
                        text_chunk = raw_content
                    extracted_texts.append(text_chunk)
                elif not page_result.get("success"):
                    print(f"⚠️  OCR page error: {page_result.get('error', 'unknown')}")
            pdf_text = "\n".join(extracted_texts).strip()
        else:
            print(f"⚠️  Typhoon OCR error {res.status_code}: {res.text}")
        print(f"📄 PDF OCR text length: {len(pdf_text)} chars")
        print(f"📄 PDF OCR preview: {pdf_text[:200]}")
    except Exception as e:
        print(f"⚠️  PDF OCR error: {e}")

    b64 = base64.b64encode(content).decode()
    file_id = f"pdf-{int(time.time() * 1000)}-{user['uid'][:6]}"

    files_ref.document(file_id).set({
        "fileId": file_id, "roomId": room_id, "uid": user["uid"],
        "username": displayName, "filename": file.filename,
        "size": len(content), "base64": b64, "textContent": pdf_text,
        "createdAt": firestore.SERVER_TIMESTAMP,
    })

    msg_ref = messages_ref.document()
    msg_ref.set({
        "roomId": room_id, "uid": user["uid"], "username": displayName, "photoURL": "",
        "text": f"📄 {file.filename}", "isPdfMsg": True, "fileId": file_id,
        "filename": file.filename, "fileSize": len(content), "hasText": len(pdf_text) > 0,
        "edited": False, "replyTo": None,
        "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP,
    })

    message = {
        "id": msg_ref.id, "roomId": room_id, "uid": user["uid"],
        "username": displayName, "photoURL": "",
        "text": f"📄 {file.filename}", "isPdfMsg": True, "fileId": file_id,
        "filename": file.filename, "fileSize": len(content), "hasText": len(pdf_text) > 0,
        "edited": False, "replyTo": None, "createdAt": now_iso(),
    }
    await broadcast({"type": "new_message", "message": message})

    if pdf_text:
        asyncio.create_task(save_pdf_embeddings({
            "fileId": file_id, "roomId": room_id, "uid": user["uid"],
            "username": displayName, "filename": file.filename, "text": pdf_text,
        }))

    print(f"📄 PDF uploaded: {file.filename} ({len(content)/1024/1024:.2f}MB) hasText={len(pdf_text) > 0}")
    return {"success": True, "message": message, "fileId": file_id, "hasText": len(pdf_text) > 0}

@app.get("/files/{file_id}/pdf")
async def get_pdf(file_id: str, user: dict = Depends(get_current_user)):
    snap = files_ref.document(file_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="File not found")
    data = snap.to_dict()
    return {"filename": data["filename"], "base64": data["base64"], "mimeType": "application/pdf", "size": data["size"]}

@app.post("/files/{file_id}/rename")
async def rename_pdf(file_id: str, user: dict = Depends(get_current_user)):
    snap = files_ref.document(file_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="File not found")
    data = snap.to_dict()
    text = data.get("textContent", "")
    filename = data.get("filename", "")

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="ไฟล์นี้ไม่มีเนื้อหาที่อ่านได้ ไม่สามารถตั้งชื่อได้")

    preview = text[:1500]
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(f"{LM}/chat/completions", json={
            "model": "google/gemma-3-4b", "stream": False,
            "temperature": 0.2, "max_tokens": 60,
            "messages": [
                {"role": "system", "content": (
                    "คุณคือผู้ช่วยตั้งชื่อไฟล์ PDF\n"
                    "ให้ตอบด้วยชื่อไฟล์ที่เหมาะสมเพียงอย่างเดียว ห้ามมีคำอธิบายอื่น\n"
                    "กฎ:\n- ตั้งชื่อเป็นภาษาไทยหรืออังกฤษตามเนื้อหา\n"
                    "- สั้น กระชับ บอกเนื้อหาหลัก ไม่เกิน 40 ตัวอักษร\n"
                    "- ไม่มีนามสกุล .pdf\n- ไม่มีอักขระพิเศษ ยกเว้น - _ และช่องว่าง\n"
                    "- ตอบแค่ชื่อไฟล์เท่านั้น ห้ามมีข้อความอื่น"
                )},
                {"role": "user", "content": f"เนื้อหาของ PDF:\n{preview}\n\nตั้งชื่อไฟล์ที่เหมาะสม:"},
            ],
        })
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail="AI ไม่ตอบสนอง")

    ai_data = res.json()
    new_name = ai_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    new_name = re.sub(r'[<>:"/\\|?*]', "", new_name)
    new_name = re.sub(r'\.pdf$', "", new_name, flags=re.IGNORECASE)
    new_name = new_name.strip('"\'').strip()[:60]

    if not new_name:
        raise HTTPException(status_code=500, detail="AI ตั้งชื่อไม่ได้")

    new_filename = f"{new_name}.pdf"
    files_ref.document(file_id).update({"filename": new_filename})

    emb_snap = embeddings_ref.where("fileId", "==", file_id).stream()
    batch = db.batch()
    for d in emb_snap:
        batch.update(d.reference, {"sourceFile": new_filename})
    batch.commit()

    msg_snap = messages_ref.where("fileId", "==", file_id).limit(1).stream()
    msg_docs = list(msg_snap)
    if msg_docs:
        msg_doc = msg_docs[0]
        msg_data = msg_doc.to_dict()
        msg_doc.reference.update({
            "filename": new_filename,
            "text": f"📄 {new_filename}",
            "updatedAt": firestore.SERVER_TIMESTAMP,
        })
        await broadcast({
            "type": "pdf_renamed", "msgId": msg_doc.id,
            "fileId": file_id, "newFilename": new_filename, "roomId": msg_data.get("roomId"),
        })

    print(f'✏️ PDF renamed: "{filename}" → "{new_filename}"')
    return {"success": True, "oldName": filename, "newName": new_filename}

# ══════════════════════════════════════════════════════
# RAG / BAIKAO
# ══════════════════════════════════════════════════════
@app.post("/rooms/{room_id}/baikao")
async def baikao(room_id: str, body: BaikaoBody, user: dict = Depends(get_current_user)):
    room_snap = rooms_ref.document(room_id).get()
    if not room_snap.exists:
        raise HTTPException(status_code=404, detail="Room not found")

    print(f"🤖 #baikao by {body.displayName}: {body.question}")

    thinking_msg = {
        "id": f"baikao-thinking-{int(time.time() * 1000)}",
        "roomId": room_id, "uid": "baikao-bot", "username": "🤖 Baikao", "photoURL": "",
        "text": "⏳ กำลังค้นหาข้อมูลและประมวลผล...",
        "edited": False, "isBot": True,
        "replyTo": body.replyTo.model_dump() if body.replyTo else None,
        "createdAt": now_iso(),
    }
    await broadcast({"type": "new_message", "message": thinking_msg})

    try:
        answer = await ragQuery(body.question, room_id, body.displayName, body.replyTo.model_dump() if body.replyTo else None)
    except Exception as e:
        answer = f"⚠️ Baikao ไม่สามารถตอบได้\n`{e}`"

    doc_ref = messages_ref.document()
    doc_ref.set({
        "roomId": room_id, "uid": "baikao-bot", "username": "🤖 Baikao", "photoURL": "",
        "text": answer, "isBot": True, "edited": False,
        "replyTo": body.replyTo.model_dump() if body.replyTo else None,
        "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP,
    })

    bot_message = {
        "id": doc_ref.id, "roomId": room_id, "uid": "baikao-bot",
        "username": "🤖 Baikao", "photoURL": "", "text": answer,
        "isBot": True, "edited": False,
        "replyTo": body.replyTo.model_dump() if body.replyTo else None,
        "createdAt": now_iso(),
    }

    await broadcast({"type": "delete_message", "id": thinking_msg["id"], "roomId": room_id})
    await broadcast({"type": "new_message", "message": bot_message})
    return {"success": True, "answer": answer}