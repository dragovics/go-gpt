# go-gpt — ChatGPT Web to OpenAI-Compatible Relay Bridge

**go-gpt** adalah bridge self-hosted yang mengubah sesi web **ChatGPT** (Plan Free / Go / Plus / Pro di `chatgpt.com`) menjadi API standar **OpenAI-Compatible** (`/v1/chat/completions` & `/v1/models`).

Dengan bridge ini, Anda dapat memanfaatkan kuota web ChatGPT langganan Anda langsung dari **VSCode (Cline / Roo Code / Continue)**, **Cursor**, **Hermes Agent**, **TITIT**, atau skrip automasi lainnya **tanpa membayar biaya token API**.

---

### ✨ Fitur Unggulan
- ⚡ **Chat Completions Lengkap**: Mendukung streaming SSE (`stream: true`) maupun non-streaming JSON standar.
- 🛠️ **Full Function / Tool Calling**: Menerima parameter `tools` dan `tool_choice`, memformat skema JSON terstruktur ke ChatGPT, dan mengembalikan `finish_reason: "tool_calls"`.
- 🛡️ **Tolerant JSON Parser**: Kebal terhadap unescaped backslash / regex bash (seperti `\(` atau `\.`) yang sering mematahkan parser JSON biasa.
- 🔄 **Auto-Reset Percakapan**: Otomatis membersihkan context / membuat chat baru agar prompt berikutnya tidak tercampur riwayat lama.
- 🌐 **Multi-Platform**: Disediakan installer 1-klik untuk **Windows 10/11** dan **Linux (Systemd)**.
- 🔒 **Privasi & Keamanan**: Menggunakan profil Chrome terisolasi (`RelayProfile` / `chrome-cdp-profile`). Cookie akun tetap tersimpan aman di mesin lokal Anda.

---

## 🏗️ Diagram Arsitektur

```text
 Client (VSCode / Cline / Roo Code / Hermes / TITIT / curl)
                        │
                        │ HTTP POST /v1/chat/completions (:20250)
                        ▼
            ┌────────────────────────┐
            │  chatgpt_relay_server  │ (FastAPI OpenAI-Compatible Adapter)
            │  - Tool Schema Injector│
            │  - Robust JSON Parser  │
            └───────────┬────────────┘
                        │ Antrean Task Internal (:20250/poll & /reply)
                        ▼
            ┌────────────────────────┐
            │    cdp_relay_driver    │ (Chrome DevTools Protocol Driver)
            │  - Auto-attach to Tab  │
            │  - Native Input & Send │
            └───────────┬────────────┘
                        │ WebSocket CDP (:9222)
                        ▼
             Google Chrome Browser (chatgpt.com)
             [Akun login ChatGPT Plan Go / Plus / Free]
```

---

## 📋 Prasyarat Sistem (Requirements)

Sebelum memulai instalasi, pastikan sistem Anda memenuhi persyaratan berikut:

1. **Sistem Operasi**:
   - **Windows**: Windows 10 atau Windows 11 (64-bit).
   - **Linux**: Ubuntu 20.04/22.04/24.04, Debian 11/12, atau distro berbasis systemd lainnya.
2. **Python**:
   - Versi **Python 3.10, 3.11, atau 3.12**.
   - *Khusus Windows:* Saat menginstall Python, **WAJIB mencentang opsi "Add python.exe to PATH"**.
3. **Browser**:
   - **Google Chrome** resmi terinstall di lokasi standar.
4. **Akun ChatGPT**:
   - Akun aktif yang bisa login di `https://chatgpt.com/` (bebas: akun Free, Plan Go, Plus, maupun Pro).
5. **Port Jaringan**:
   - Port `20250` (Port HTTP API Relay) harus bebas / tidak terpakai aplikasi lain.
   - Port `9222` (Port internal Chrome Remote Debugging).

---

## 🚀 Panduan Instalasi Step-by-Step

### OPSI 1: Panduan untuk Pengguna Windows (10 / 11)

Tidak memerlukan hak akses Administrator (cukup user biasa).

#### Langkah 1: Download & Siapkan File
Clone repository ini atau download dan ekstrak file ZIP:
```cmd
git clone https://github.com/dragovics/go-gpt.git
cd go-gpt
```

#### Langkah 2: Jalankan Installer
Dobel klik file **`install_windows.bat`** (atau `scripts\windows\install.bat`).
- Script akan otomatis membuat virtual environment Python (`venv`).
- Menginstall dependensi: `fastapi`, `uvicorn`, `websockets`, `httpx`.
- Menyiapkan folder kerja di `%USERPROFILE%\chatgpt-relay`.

#### Langkah 3: Buka Browser Chrome Khusus Relay
Dobel klik file **`scripts\windows\start_chrome.bat`**.
- Browser Google Chrome khusus akan terbuka dengan profil terpisah (`RelayProfile`).
- Buka tab `https://chatgpt.com/` dan **login ke akun ChatGPT Anda**.
- *Catatan:* Biarkan jendela Chrome ini tetap terbuka di background selama Anda menggunakan relay.

#### Langkah 4: Jalankan Relay Server
Dobel klik file **`scripts\windows\start_relay.bat`**.
- Akan muncul 2 jendela konsol terminal kecil:
  1. `ChatGPT Server (:20250)` — Server API FastAPI.
  2. `ChatGPT CDP Driver` — Driver penghubung ke browser.
- Jendela konsol akan menampilkan pesan: `[driver] attached to tab`.

#### Langkah 5: Verifikasi Status
Buka browser Anda dan akses URL status:
```text
http://localhost:20250/status
```
Jika berhasil, response JSON akan menampilkan:
```json
{"status": "online", "tab_url": "https://chatgpt.com/..."}
```

---

### OPSI 2: Panduan untuk Pengguna Linux (Ubuntu / Debian VPS)

Jalankan script instalasi sebagai root (atau gunakan `sudo`):

```bash
git clone https://github.com/dragovics/go-gpt.git
cd go-gpt
sudo bash install_linux.sh
```

**Yang dilakukan script secara otomatis:**
1. Menyiapkan Python virtual environment di `/home/<user>/worker-env`.
2. Menginstall library `fastapi`, `uvicorn`, `websockets`, `httpx`.
3. Memasang dan mengaktifkan 2 service `systemd`:
   - `chatgpt-relay.service` (Server API port `20250`)
   - `chatgpt-cdp-driver.service` (CDP Driver)
4. Membuat script launcher Chrome di `/home/<user>/bin/start_chrome_cdp.sh`.

**Langkah selanjutnya:**
1. Jalankan Chrome dengan remote debugging:
   ```bash
   /home/<user>/bin/start_chrome_cdp.sh &
   ```
2. Buka antarmuka desktop/GUI (lewat VNC atau NoVNC), lalu **login ke akun ChatGPT** Anda di jendela Chrome tersebut.
3. Verifikasi status relay:
   ```bash
   curl http://127.0.0.1:20250/status
   # Hasil: {"status":"online","tab_url":"https://chatgpt.com/..."}
   ```

---

## 🔌 Cara Menghubungkan ke VSCode & Agent

Setelah status relay **`online`**, Anda bisa langsung menghubungkannya ke berbagai tool:

### 1. VSCode — Ekstensi Cline / Roo Code (Rekomendasi untuk Coding Agent)
Cline dan Roo Code membutuhkan model yang mendukung Tool Calling untuk membaca file, mengedit kode, dan mengeksekusi terminal.

1. Buka ekstensi **Cline** / **Roo Code** di VSCode.
2. Klik ikon **Settings** (roda gigi) di pojok atas panel ekstensi.
3. Atur konfigurasi berikut:
   - **API Provider**: Pilih `OpenAI Compatible`
   - **Base URL**: `http://localhost:20250/v1` *(atau `http://IP_VPS:20250/v1` jika relay di VPS)*
   - **API Key**: `dummy` *(bebas, relay lokal tidak memvalidasi key)*
   - **Model ID**: `chatgpt-web` *(atau `gpt-6-luna`)*
4. Klik **Done / Save**. Selesai! Cline sekarang berjalan menggunakan otak ChatGPT Web Anda.

---

### 2. VSCode — Ekstensi Continue (Sidebar Chat & Autocomplete)
1. Buka file konfigurasi Continue (`~/.continue/config.json`).
2. Masukkan blok model berikut ke dalam array `"models"`:
```json
{
  "models": [
    {
      "title": "ChatGPT Web Local",
      "provider": "openai",
      "model": "chatgpt-web",
      "apiBase": "http://localhost:20250/v1",
      "apiKey": "dummy"
    }
  ]
}
```
3. Simpan file, lalu pilih model **ChatGPT Web Local** di panel Continue.

---

### 3. Integrasi ke 9Router / LLM Gateway
Jika Anda menggunakan gateway seperti 9Router:
- **Provider Type**: `openai-compatible`
- **Prefix**: `go`
- **Base URL**: `http://127.0.0.1:20250/v1`
- **Models**: `chatgpt-web`, `gpt-6-luna`
- *Panggilan agent*: `go/chatgpt-web`

---

## 🧪 Pengujian via cURL

### 1. Test Chat Sederhana (Streaming)
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

### 2. Test Function / Tool Calling
```bash
curl -s -X POST http://localhost:20250/v1/chat/completions \
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
          "name": "cek_uptime",
          "description": "Mengecek uptime dan beban load server Linux",
          "parameters": {
            "type": "object",
            "properties": {}
          }
        }
      }
    ]
  }' | jq .
```

**Respon JSON yang dihasilkan (OpenAI Standard):**
```json
{
  "id": "chatcmpl-59ef38e9",
  "object": "chat.completion",
  "created": 1790708064,
  "model": "chatgpt-web",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_5e6313f3abe4",
            "type": "function",
            "function": {
              "name": "cek_uptime",
              "arguments": "{}"
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

## ❓ FAQ & Troubleshooting

### Q1: Status menunjukkan `{"status": "offline"}`?
- **Penyebab:** Driver belum menemukan tab browser `chatgpt.com` yang terbuka dengan port debugging `9222`.
- **Solusi:**
  1. Pastikan Chrome dibuka menggunakan file `start_chrome.bat` (Windows) atau `start_chrome_cdp.sh` (Linux).
  2. Pastikan tab `https://chatgpt.com/` aktif dan tidak tertutup.

### Q2: Di Windows muncul pesan `'python' is not recognized as an internal or external command`?
- **Penyebab:** Python belum ditambahkan ke System Environment Variables (PATH).
- **Solusi:** Buka installer Python, pilih **Modify**, lalu centang opsi **"Add Python to PATH"**. Setelah itu restart Command Prompt / Terminal Anda.

### Q3: Apakah aman untuk akun ChatGPT saya?
- **Sangat Aman:** Tidak ada kredensial, username, password, atau token sesi yang dikirim ke server pihak ketiga. Semua komunikasi browser dikendalikan secara lokal di komputer Anda sendiri melalui Chrome DevTools Protocol (CDP).

### Q4: Bagaimana cara mematikan relay jika sudah selesai digunakan?
- **Windows:** Cukup tutup kedua jendela konsol terminal (`ChatGPT Server` dan `ChatGPT CDP Driver`).
- **Linux:** Jalankan `sudo systemctl stop chatgpt-relay chatgpt-cdp-driver`.

---

## 📄 Lisensi
Didistribusikan di bawah lisensi MIT.
