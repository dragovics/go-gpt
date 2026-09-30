# go-gpt runtime — Production-Grade Tool-Calling Agent Runtime

Runtime Python untuk agent loop `LLM → tool_call → dispatcher → tool_result → LLM → final answer`,
lengkap dengan lapisan keamanan deterministik. Kompatibel dengan endpoint OpenAI apa pun —
termasuk relay ChatGPT Web (`go/chatgpt-web`) dan 9Router.

## Arsitektur

```
User
  ↓
Agent Loop            (agent.py)      turn budget, loop guard, history
  ↓
Provider / LLM API    (provider.py)   OpenAI-compatible, tolerant JSON
  ↓
Tool Calls
  ↓
Schema Validator      (validator.py)  tipe + required, error-as-data
  ↓
Dispatcher            (dispatcher.py) registry, audit, exception isolation
  ↓
Safety Layer          (safety.py)     deny-pattern, path jail, env scrub, output cap
  ↓
Tool Executor         (tools/)        terminal, execute_code, read/write/list
  ↓
Tool Result
  ↓
Agent Loop → LLM
  ↓
Final Answer
```

## Struktur

```
runtime/
├── config.py            # env-driven config: model, batas, deny-list, secret keys
├── provider.py          # call_model(messages, tools) -> ModelResponse
├── agent.py             # agent loop: turn budget, loop guard, refusal handling
├── dispatcher.py        # registry + validasi + audit + exception isolation
├── validator.py         # JSON-schema validation (required + tipe)
├── safety.py            # path confinement, command deny, env scrub, truncation
├── schemas.py           # helper katalog schema
├── main.py              # CLI entrypoint
├── tools/
│   ├── base.py          # BaseTool + to_openai_schema()
│   ├── terminal.py      # bash: timeout, safe env, output cap
│   ├── python_exec.py   # python subprocess terisolasi
│   └── files.py         # read_file / write_file / list_dir (workspace jail)
├── state/
│   ├── session.py       # SQLite WAL: riwayat per session_id
│   └── audit.py         # JSONL append-only, argumen sensitif diredaksi
└── tests/
    ├── test_safety.py         # 20 test
    ├── test_dispatcher.py     # 16 test
    ├── test_tools.py          # 16 test
    ├── test_agent.py          # 16 test (FakeProvider, offline & deterministik)
    └── hostile_injection.py   # injeksi tool_call jahat langsung ke dispatcher
```

## Lapisan keamanan

| Lapisan | Mekanisme | Implementasi |
|---|---|---|
| Deny-pattern | `rm -rf /`, fork bomb, `mkfs`, `dd`, `shutdown`, redirect ke raw device | `safety.check_command_safety` |
| Path jail | semua path file di-resolve dan wajib di dalam workspace | `safety.check_path_confinement` |
| Env scrub | `OPENAI_API_KEY`, `VAULT_TOKEN`, `*_PRIVATE_KEY`, dll dibuang sebelum subprocess | `safety.sanitize_env` |
| Output cap | head+tail dengan penanda truncation eksplisit | `safety.truncate_output` |
| Schema validation | required + tipe, `bool` bukan `integer` | `validator.validate_arguments` |
| Error-as-data | tool gagal → string error ke model, proses tidak mati | `dispatcher.dispatch` |
| Turn budget | `MAX_TURNS` (default 15) | `agent.Agent.run` |
| Loop guard | tool call identik > `repeat_limit` → diblok + diberi peringatan | `agent.Agent.run` |
| Audit trail | JSONL append-only, argumen `*key*`/`*pass*`/`*token*`/`*secret*` diredaksi | `state/audit.py` |
| Timeout | per-tool, proses di-kill saat lewat batas | `tools/terminal.py`, `tools/python_exec.py` |

**Catatan penting:** `terminal` memberi kemampuan menjalankan apa pun yang diizinkan user Linux
yang menjalankan runtime. Kalau runtime diekspos ke user lain, jalankan sebagai user terbatas
atau di dalam container — deny-list bukan pengganti isolasi OS.

## Instalasi

```bash
cd go-gpt
python3 -m venv runtime-env
./runtime-env/bin/pip install pytest pytest-asyncio   # hanya untuk test; runtime pakai stdlib
```

Runtime sendiri **nol dependency eksternal** (`urllib`, `sqlite3`, `asyncio` dari stdlib).

## Konfigurasi (environment variables)

| Variable | Default | Keterangan |
|---|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:20128/v1` | endpoint OpenAI-compatible |
| `LLM_API_KEY` | `dummy-key` | bearer token |
| `LLM_MODEL` | `go/chatgpt-web` | id model |
| `WORKSPACE_DIR` | `./workspace` | batas keras semua operasi file |
| `MAX_TURNS` | `15` | batas iterasi agent loop |
| `TOOL_TIMEOUT` | `30.0` | timeout default per tool (detik) |
| `MAX_OUTPUT_BYTES` | `32768` | batas output tool sebelum truncation |
| `AUDIT_LOG_PATH` | `./audit.jsonl` | jalur audit trail |
| `SESSION_DB_PATH` | `./sessions.db` | SQLite riwayat percakapan |

## Pemakaian

```bash
export LLM_BASE_URL="http://127.0.0.1:20128/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="cc/claude-opus-5"
export WORKSPACE_DIR="./workspace"

./runtime-env/bin/python -m runtime.main \
  "Buat file report.txt berisi hostname dan tanggal, lalu baca ulang dan laporkan isinya."
```

Opsi CLI: `--session ID`, `--model`, `--base-url`, `--workspace`, `--max-turns`, `--verbose`.

Session persistence: pakai `--session` yang sama untuk melanjutkan percakapan sebelumnya.

## Menambah tool baru

```python
from runtime.tools.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "Apa yang tool ini lakukan."
    parameters = {
        "type": "object",
        "properties": {"arg": {"type": "string"}},
        "required": ["arg"],
    }

    async def execute(self, **kwargs) -> str:
        return f"hasil: {kwargs['arg']}"
```

Register di `main.build_runtime()`. Validasi schema, audit, timeout, dan exception isolation
otomatis berlaku.

## Test

```bash
./runtime-env/bin/python -m pytest                          # 68 test unit/integrasi
./runtime-env/bin/python -m runtime.tests.hostile_injection  # 14 serangan + 5 legit
```

Test unit sepenuhnya offline (`FakeProvider` bersrip), tidak memanggil LLM nyata.

## Hasil verifikasi nyata

Unit & integrasi:

```
68 passed in 11.42s
```

Hostile injection (injeksi tool_call jahat langsung ke dispatcher):

```
attacks blocked      : 14/14
legitimate preserved : 5/5
FILESYSTEM INTEGRITY : /etc/passwd exists PASS | canary survived PASS
                       no escape above workspace PASS | workspace write worked PASS
VERDICT              : PASS
```

E2E dengan `cc/claude-opus-5` (4 turn, 4 tool call, file benar-benar dibuat):

```
[runtime] stop_reason=final_answer
[runtime] turns_used=4 tool_calls=4
terminal     ok=True  0.056s {'command': 'hostname'}
terminal     ok=True  0.038s {'command': "date '+%Y-%m-%d'"}
write_file   ok=True  0.001s {'path': 'report.txt', ...}
read_file    ok=True  0.000s {'path': 'report.txt'}
```

E2E output flood (`MAX_OUTPUT_BYTES=4096`, output 200.000 B):

```
execute_code ok=True out=4167B   # terpotong dari 200000 B, model sadar terpotong
terminal     ok=True out=3B      # ls /etc | wc -l -> 225
```

E2E via relay ChatGPT Go (kuota gratis, `go/chatgpt-web`):

```
[runtime] stop_reason=final_answer
[runtime] turns_used=2 tool_calls=1   (31.4s)
```
