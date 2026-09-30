# go-gpt — ChatGPT Web to OpenAI-Compatible Relay Bridge

**go-gpt** adalah bridge bridge self-hosted yang mengubah sesi web **ChatGPT** (Plan Free / Go / Plus / Pro di `chatgpt.com`) menjadi API standar **OpenAI-Compatible** (`/v1/chat/completions` & `/v1/models`).

Mendukung penuh:
- ⚡ **Chat completions** (Streaming SSE `stream: true` & Non-streaming)
- 🛠️ **Full Function / Tool Calling** (format standar OpenAI `tools`, `tool_choice`, dan pesan balik `role: "tool"`)
- 🛡️ **Tolerant JSON parser** (kebal terhadap unescaped backslash/regex command bash seperti `\(` atau `\.`)
- 🌐 **Multi-Platform**: Siap jalan di **Linux (Systemd)** & **Windows 10/11 (Batch Runner)**
- 🔌 **Seamless Integration**: Langsung dicolok ke VSCode (Cline, Roo Code, Continue), Cursor, 9Router, Hermes Agent, dan TITIT.

---

## 🏗️ Arsitektur

```text
 Client (VSCode / Hermes / TITIT / curl)
                 │
                 │ HTTP POST /v1/chat/completions (:20250)
                 ▼
     ┌────────────────────────┐
     │ chatgpt_relay_server   │ (FastAPI OpenAI-Compatible Adapter)
     │ - Tool Schema Injector │
     │ - Robust Output Parser │
     └───────────┬────────────┘
                 │ Long-polling task queue (:20250/poll & /reply)
                 ▼
     ┌────────────────────────┐
     │ cdp_relay_driver       │ (Chrome DevTools Protocol Automation)
     │ - Auto-attach to Tab   │
     │ - Native Typing / Send │
     └───────────┬────────────┘
                 │ Chrome DevTools Protocol (:9222)
                 ▼
      Google Chrome Browser (chatgpt.com)
      [Akun login ChatGPT Plan Go / Plus / Free]
```

---

## 🚀 Panduan Instalasi

### Opsi A: Linux (Ubuntu / Debian VPS)

Jalankan script all-in-one installer sebagai root:

```bash
sudo bash install_linux.sh
```

Script otomatis:
1. Menyiapkan Python virtual environment (`worker-env`).
2. Menginstall dependensi: `fastapi`, `uvicorn`, `websockets`, `httpx`.
3. Memasang 2 service systemd:
   - `chatgpt-relay.service` (Server API di port `20250`)
   - `chatgpt-cdp-driver.service` (Otomatisasi browser via CDP)
4. Membuat script launcher Chrome di `~/bin/start_chrome_cdp.sh`.

**Langkah selanjutnya di Linux:**
1. Jalankan Chrome dengan remote debugging:
   ```bash
   ~/bin/start_chrome_cdp.sh &
   ```
2. Buka GUI (VNC / Desktop), login ke akun `chatgpt.com` di jendela Chrome tersebut.
3. Cek status:
   ```bash
   curl http://127.0.0.1:20250/status
   # Output: {"status":"online","tab_url":"https://chatgpt.com/..."}
   ```

---

### Opsi B: Windows 10 / 11

Tidak butuh akses Administrator, cukup Python 3.10+ sudah terpasang.

1. **Jalankan Installer:**
   - Dobel klik file `install_windows.bat` (atau jalankan `scripts/windows/install.bat`).
2. **Buka Chrome Khusus Relay:**
   - Dobel klik `scripts/windows/start_chrome.bat`.
   - Browser Chrome akan terbuka dengan profil khusus. Login ke akun `chatgpt.com`. Biarkan tab tetap terbuka.
3. **Jalankan Relay Server:**
   - Dobel klik `scripts/windows/start_relay.bat`.
   - Dua jendela konsol akan aktif (Server port `20250` dan CDP Driver).
4. Cek status di browser: `http://localhost:20250/status`.

---

## 🔌 Panduan Koneksi ke Client

### 1. VSCode (Cline / Roo Code)
Ekstensi coding agent dengan kemampuan eksekusi terminal dan edit file:
- **API Provider**: `OpenAI Compatible`
- **Base URL**: `http://localhost:20250/v1` (atau IP server VPS: `http://IP_VPS:20250/v1`)
- **API Key**: `dummy` (bebas, tidak divalidasi)
- **Model ID**: `chatgpt-web`

### 2. VSCode (Continue)
Tambahkan ke konfigurasi `~/.continue/config.json`:
```json
{
  "models": [
    {
      "title": "ChatGPT Web Relay",
      "provider": "openai",
      "model": "chatgpt-web",
      "apiBase": "http://localhost:20250/v1",
      "apiKey": "dummy"
    }
  ]
}
```

### 3. Integrasi 9Router
Daftarkan sebagai node `openai-compatible`:
- **Prefix**: `go/`
- **Base URL**: `http://159.223.79.179:20250/v1`
- **Model ID**: `go/chatgpt-web` atau `go/gpt-6-luna`

---

## 🧪 Contoh Pengujian via cURL

### 1. Chat Biasa (Streaming SSE)
```bash
curl -N http://localhost:20250/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "chatgpt-web",
    "messages": [
      {"role": "user", "content": "Halo, tes koneksi relay!"}
    ],
    "stream": true
  }'
```

### 2. Tool Calling (Function Calling)
```bash
curl -s http://localhost:20250/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "chatgpt-web",
    "messages": [
      {"role": "user", "content": "Tolong cek berapa uptime server sekarang"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "terminal",
          "description": "Menjalankan perintah bash di server",
          "parameters": {
            "type": "object",
            "properties": {
              "command": {"type": "string"}
            },
            "required": ["command"]
          }
        }
      }
    ]
  }' | jq .
```

**Respon OpenAI-standard yang dihasilkan:**
```json
{
  "id": "chatcmpl-32355fc5",
  "object": "chat.completion",
  "created": 1790738171,
  "model": "chatgpt-web",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_f04a4bc8ae09",
            "type": "function",
            "function": {
              "name": "terminal",
              "arguments": "{\"command\": \"uptime\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

---

## 📄 Lisensi
MIT License. Dibuat untuk pemanfaatan komputasi personal & automasi AI agent.
