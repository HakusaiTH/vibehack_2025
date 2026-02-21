# 🚀 Baikao Chat AI

**Baikao Chat** คือแพลตฟอร์มแชทอัจฉริยะ ที่รวม

* 💬 Real-time Chat
* 🤖 AI ตอบคำถามจากความรู้ทีม
* 🎙️ Speech-to-Text (ไทย/อังกฤษ)
* 📄 PDF OCR + AI Search
* 🔒 Firebase Authentication

ไว้ในที่เดียว

---

# 🧠 Features

## 💬 Real-Time Chat

* WebSocket communication
* Chat rooms
* Instant messaging
* AI assistant (#baikao)

---

## 🤖 AI Assistant (Local LLM)

ใช้

* Gemma 3 4B
* LM Studio

สามารถ:

* ตอบคำถาม
* วิเคราะห์ข้อความ
* สรุปบทสนทนา

---

## 🎙️ Speech-to-Text

Upload:

* .webm
* .mp3
* .wav

ระบบจะ:

→ แปลงเป็นข้อความ

รองรับ:

* ภาษาไทย
* ภาษาอังกฤษ

---

## 📄 PDF OCR

Upload PDF

AI จะ:

* อ่าน
* Extract text
* ให้ query ได้

---

## 🔒 Authentication

ใช้ Firebase Auth:

* Email / Password
* Google Login

---

# 🏗 Architecture

```
Frontend (HTML / React)
     ↓
WebSocket
     ↓
FastAPI Backend
     ↓
LM Studio (Local LLM)
```

Speech flow:

```
Frontend
   ↓
STT Server
   ↓
Text
   ↓
Chat Backend
```

---

# 📁 Project Structure

```
vibehack/

backend/
│
├── main.py
├── stt_server.py

front/
│
├── index.html

cloudflared.exe

README.md
```

---

# ⚙️ Requirements

Install:

* Python 3.10+
* pip
* LM Studio
* ffmpeg

---

# 📦 Install Dependencies

```
cd backend
```

install:

```
pip install fastapi uvicorn
pip install websockets
pip install python-multipart
pip install faster-whisper
pip install ffmpeg-python
pip install firebase-admin
pip install httpx
```

---

# 🤖 Start LM Studio

Open LM Studio

Load model:

```
Gemma 3 4B
```

Start server:

```
http://localhost:3000
```

---

# 🚀 Start Backend Server

```
cd backend

uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

Backend URL:

```
http://localhost:3000
```

WebSocket:

```
ws://localhost:3000/ws
```

---

# 🎙 Start STT Server

```
cd backend

python stt_server.py
```

Server:

```
http://localhost:5001
```

Test:

```
http://localhost:5001/health
```

---

# 🌐 Start Frontend

```
cd front

python -m http.server 8081
```

open:

```
http://localhost:8081
```

---

# 🔧 Frontend Config

ใน index.html:

```
const API     = 'http://localhost:3000';
const WS_URL  = 'ws://localhost:3000/ws';
const STT_URL = 'http://localhost:5001';
```

---

# 🌍 Public Deploy (Cloudflare Tunnel)

run:

```
cloudflared.exe tunnel --url http://localhost:8081
```

example:

```
https://xxxx.trycloudflare.com
```

---

# 🔥 Ports Used

| Service   | Port |
| --------- | ---- |
| Frontend  | 8081 |
| Backend   | 3000 |
| STT       | 5001 |
| LM Studio | 3000 |

---

# 🧪 How to Use

1 Open Web

```
http://localhost:8081
```

2 Login

3 Chat

4 Use mic 🎙

5 Ask AI:

```
#baikao สรุปการประชุม
```

---

# 🧠 Tech Stack

Frontend:

* HTML
* JavaScript
* WebSocket
* Firebase SDK

Backend:

* FastAPI
* Python

AI:

* Gemma 3
* LM Studio

STT:

* Faster Whisper

Database:

* Firebase

---

# 👨‍💻 Developer

Baikao Team

Vibe Hackathon 2026

---

# 🚀 Future Plan

* Mobile app
* Image support
* Vector search
* Multi workspace
* Enterprise version

---

# ❤️ License

MIT License

---
