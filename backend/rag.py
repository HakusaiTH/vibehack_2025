"""
rag.py — RAG utilities (แปลงจาก rag.ts)
- getEmbedding      → get_embedding
- cosineSimilarity  → cosine_similarity
- saveMessageEmbedding → save_message_embedding
- savePdfEmbeddings    → save_pdf_embeddings
- retrieveRelevant     → retrieve_relevant
- ragQuery             → ragQuery
"""

import math
from datetime import datetime, timezone
from typing import Optional

import httpx
from firebase_admin import firestore

LM = "http://localhost:1234/v1"

# ══════════════════════════════════════════════════════
# DB (lazy singleton)
# ══════════════════════════════════════════════════════
_db = None

def get_db():
    global _db
    if _db is None:
        _db = firestore.client()
    return _db

# ══════════════════════════════════════════════════════
# EMBEDDING
# ══════════════════════════════════════════════════════
async def get_embedding(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(f"{LM}/embeddings", json={
            "model": "text-embedding-nomic-embed-text-v1.5",
            "input": text,
        })
    if res.status_code != 200:
        raise RuntimeError(f"Embedding failed: {res.text}")
    data = res.json()
    return data["data"][0]["embedding"]

# ══════════════════════════════════════════════════════
# COSINE SIMILARITY
# ══════════════════════════════════════════════════════
def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b + 1e-10)

# ══════════════════════════════════════════════════════
# SAVE MESSAGE EMBEDDING
# ══════════════════════════════════════════════════════
async def save_message_embedding(msg: dict):
    """
    msg keys: id, roomId, uid, username, text, createdAt
    """
    try:
        embedding = await get_embedding(msg["text"])
        get_db().collection("embeddings").document(msg["id"]).set({
            "msgId":     msg["id"],
            "roomId":    msg["roomId"],
            "uid":       msg["uid"],
            "username":  msg["username"],
            "text":      msg["text"],
            "isPdf":     False,
            "embedding": embedding,
            "createdAt": msg["createdAt"],
            "indexedAt": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"⚠️  Embedding skipped: {e}")

# ══════════════════════════════════════════════════════
# SAVE PDF EMBEDDINGS — chunk + embed
# ══════════════════════════════════════════════════════
async def save_pdf_embeddings(opts: dict):
    """
    opts keys: fileId, roomId, uid, username, filename, text
    """
    file_id  = opts["fileId"]
    room_id  = opts["roomId"]
    uid      = opts["uid"]
    username = opts["username"]
    filename = opts["filename"]
    text     = opts["text"]

    CHUNK, OVERLAP = 500, 80
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunk = text[i:i + CHUNK].strip()
        if chunk:
            chunks.append(chunk)
        i += CHUNK - OVERLAP

    now = datetime.now(timezone.utc).isoformat()

    for ci, chunk in enumerate(chunks):
        try:
            embedding = await get_embedding(chunk)
            get_db().collection("embeddings").document(f"{file_id}-chunk-{ci}").set({
                "msgId":      f"{file_id}-chunk-{ci}",
                "roomId":     room_id,
                "uid":        uid,
                "username":   username,
                "text":       chunk,
                "sourceFile": filename,
                "fileId":     file_id,
                "isPdf":      True,
                "chunkIndex": ci,
                "embedding":  embedding,
                "createdAt":  now,
                "indexedAt":  firestore.SERVER_TIMESTAMP,
            })
        except Exception as e:
            print(f"⚠️ PDF chunk {ci} embed failed: {e}")

    print(f'📄 PDF "{filename}" → {len(chunks)} chunks embedded')

# ══════════════════════════════════════════════════════
# RETRIEVE RELEVANT
# ══════════════════════════════════════════════════════
async def retrieve_relevant(query: str, room_id: str, top_k: int = 12) -> list[dict]:
    query_emb = await get_embedding(query)
    snap = get_db().collection("embeddings").where("roomId", "==", room_id).stream()
    docs = list(snap)
    if not docs:
        return []

    results = []
    for d in docs:
        data = d.to_dict()
        score = cosine_similarity(query_emb, data.get("embedding", []))
        results.append({
            "text":       data.get("text", ""),
            "username":   data.get("username", ""),
            "createdAt":  data.get("createdAt", ""),
            "sourceFile": data.get("sourceFile"),
            "isPdf":      data.get("isPdf"),
            "score":      score,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return [r for r in results[:top_k] if r["score"] > 0.25]

# ══════════════════════════════════════════════════════
# RAG QUERY
# ══════════════════════════════════════════════════════
async def ragQuery(
    question: str,
    room_id: str,
    asker: str,
    reply_to: Optional[dict] = None,
) -> str:
    relevant = await retrieve_relevant(question, room_id)

    reply_context = ""
    if reply_to:
        if reply_to.get("isPdfMsg"):
            reply_context = f"[ข้อความที่ {asker} ต้องการให้ตอบ]\n📄 ไฟล์: {reply_to.get('filename') or 'document.pdf'}\n\n"
        else:
            reply_context = f"[ข้อความที่ {asker} ต้องการให้ตอบ]\n{reply_to.get('username')}: {reply_to.get('text')}\n\n"

    if relevant:
        sorted_rel = sorted(relevant, key=lambda r: r.get("createdAt") or "")
        lines = []
        for r in sorted_rel:
            if r.get("isPdf") and r.get("sourceFile"):
                source = f"[📄 {r['sourceFile']}]"
            else:
                try:
                    dt = datetime.fromisoformat(r["createdAt"]).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    dt = r.get("createdAt", "")
                source = f"[{dt}] {r['username']}"
            lines.append(f"{source}: {r['text']}")
        context_block = "\n".join(lines)
    else:
        context_block = "(ไม่พบข้อมูลที่เกี่ยวข้องในห้องนี้)"

    system_prompt = (
        "คุณคือ Baikao (ไบเก้า) ผู้ช่วย AI ในระบบ chat\n"
        "คุณมีหน้าที่ตอบคำถามโดยอ้างอิงจากบทสนทนาและเอกสาร PDF ที่อยู่ในห้องนี้\n"
        "ถ้ามี [ข้อความที่ต้องการให้ตอบ] ให้โฟกัสตอบเกี่ยวกับข้อความนั้นเป็นหลัก\n"
        "ตอบเป็นภาษาไทยเสมอ กระชับ ชัดเจน ถ้าข้อมูลมาจาก PDF ให้บอกด้วยว่ามาจากไฟล์อะไร\n"
        "ถ้าไม่มีข้อมูลที่เกี่ยวข้อง ให้บอกตรงๆ ว่าไม่พบข้อมูล"
    )
    user_prompt = f"{reply_context}ข้อมูลที่เกี่ยวข้อง:\n{context_block}\n\n---\nคำถามจาก {asker}: {question}"

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(f"{LM}/chat/completions", json={
            "model": "google/gemma-3-4b",
            "stream": False,
            "temperature": 0.3,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        })
    if res.status_code != 200:
        raise RuntimeError(f"LLM error: {res.text}")
    data = res.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "ไม่สามารถตอบได้ในขณะนี้").strip()