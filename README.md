const API     = 'http://localhost:3000'; <-- lmstudio 
const WS_URL  = 'ws://localhost:3000/ws'; <--  main:app
const STT_URL = 'http://localhost:5001'; <-- stt_server.py


\backend>uvicorn main:app --host 0.0.0.0 --port 3000 --reload
\backend>python stt_server.py
