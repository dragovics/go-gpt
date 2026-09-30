#!/usr/bin/env bash
# ==============================================================================
# Setup ChatGPT Web Relay Bridge with FULL Tool Calling Support
# Port : 20250 -> OpenAI-compatible API (/v1/chat/completions)
# Bridge ChatGPT Web (chatgpt.com) to Agent Tool Calling (OpenAI Function Calling)
# ==============================================================================
set -e

TARGET_USER="${SUDO_USER:-azis}"
USER_HOME=$(eval echo "~${TARGET_USER}")
VENV_PYTHON="${USER_HOME}/worker-env/bin/python"
VENV_PIP="${USER_HOME}/worker-env/bin/pip"
RELAY_PORT=20250
CDP_PORT=9222
DISPLAY_NUM=":1"

echo "=== [1/5] Verifikasi Environment ==="
if [ "$EUID" -ne 0 ]; then
  echo "[-] Jalankan script ini sebagai root (atau sudo bash $0)"
  exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
  echo "[-] Interpreter virtualenv tidak ditemukan di: $VENV_PYTHON"
  echo "    Buat venv terlebih dahulu: sudo -u $TARGET_USER python3 -m venv ${USER_HOME}/worker-env"
  exit 1
fi

echo "[+] Menginstall dependensi Python..."
sudo -u "$TARGET_USER" "$VENV_PIP" install -q fastapi uvicorn websockets httpx

echo "=== [2/5] Menulis chatgpt_relay_server.py (Tool Calling Shim + OpenAI API) ==="
cat << 'EOF' > "${USER_HOME}/chatgpt_relay_server.py"
import asyncio
import json
import re
import time
import uuid
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="ChatGPT-Web-Relay-ToolCalling")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

pending_queue = asyncio.Queue()
results: Dict[str, asyncio.Future] = {}
browser_status = {"last_seen": 0, "tab_url": None, "status": "offline"}


def parse_json_safely(s: str):
    try:
        return json.loads(s, strict=False)
    except Exception:
        pass
    fixed = re.sub(r'\\(?![/"\\bfnrtu]|u[0-9a-fA-F]{4})', r'\\\\', s)
    try:
        return json.loads(fixed, strict=False)
    except Exception:
        pass
    return None

def parse_tool_calls_from_text(text: str, available_tools: list) -> Optional[List[Dict[str, Any]]]:
    """Ekstrak tool call JSON dari respon teks mentah ChatGPT Web."""
    text = (text or "").strip()
    available_names = set()
    if available_tools:
        for t in available_tools:
            if isinstance(t, dict) and "function" in t and "name" in t["function"]:
                available_names.add(t["function"]["name"])

    # Cari blok kode markdown ```json ... ``` atau ``` ... ```
    code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    candidates = code_blocks + [text]

    for cand in candidates:
        cand = cand.strip()
        start_idx = cand.find("{")
        end_idx = cand.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            sub = cand[start_idx:end_idx+1]
            try:
                obj = parse_json_safely(sub)
                calls = []
                if isinstance(obj, dict):
                    if "tool_calls" in obj and isinstance(obj["tool_calls"], list):
                        for item in obj["tool_calls"]:
                            name = item.get("name") or item.get("tool") or item.get("function")
                            args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
                            if not available_names or name in available_names:
                                calls.append({"name": name, "arguments": args})
                    elif "name" in obj and ("arguments" in obj or "args" in obj or "parameters" in obj):
                        name = obj.get("name")
                        args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
                        if not available_names or name in available_names:
                            calls.append({"name": name, "arguments": args})
                    elif "tool" in obj:
                        name = obj.get("tool")
                        args = obj.get("arguments") or obj.get("args") or {}
                        if not available_names or name in available_names:
                            calls.append({"name": name, "arguments": args})
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, dict):
                            name = item.get("name") or item.get("tool") or item.get("function")
                            args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
                            if name and (not available_names or name in available_names):
                                calls.append({"name": name, "arguments": args})
                if calls:
                    return calls
            except Exception:
                pass
    return None

def format_messages_with_tools(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """Format riwayat chat dan inject instruksi tool calling ke prompt."""
    prompt_lines = []

    # 1. Inject schema tools jika ada
    if tools:
        tool_schemas = []
        for t in tools:
            if isinstance(t, dict) and t.get("type") == "function" and "function" in t:
                f = t["function"]
                tool_schemas.append({
                    "name": f.get("name"),
                    "description": f.get("description", ""),
                    "parameters": f.get("parameters", {})
                })
        
        system_tool_prompt = (
            "[TASK: FUNCTION ORCHESTRATION & DISPATCH]\n"
            "You are acting as an API Function Calling Gateway.\n"
            "You are provided with a list of callable functions:\n"
            f"{json.dumps(tool_schemas, indent=2)}\n\n"
            "OPERATING PROTOCOL:\n"
            "1. When the user requests an action, command, investigation, or data retrieval that maps to any function above, generate the appropriate function invocation payload.\n"
            "2. Do NOT execute or simulate the tool yourself. Do NOT explain or apologize. The external caller handles execution automatically.\n"
            "3. Output MUST be ONLY a raw JSON block in this exact structure:\n"
            "```json\n"
            "{\n"
            '  "tool_calls": [\n'
            '    {\n'
            '      "name": "<function_name>",\n'
            '      "arguments": { <parameters_dict> }\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "```\n"
            "4. If a tool result ([TOOL RESULT]) is already present in the history, use that result to write the final conversational answer to the user. Do not call the same tool repeatedly.\n"
            "5. If no function matches the user inquiry, provide a normal conversational answer.\n"
            "--------------------------------------------------"
        )
        prompt_lines.append(system_tool_prompt)

    # 2. Format riwayat pesan (termasuk hasil eksekusi tool sebelumnya)
    for msg in messages[-8:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join([p.get("text", "") for p in content if isinstance(p, dict)])

        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            calls_summary = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                calls_summary.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
            prompt_lines.append(f"[ASSISTANT CALLED TOOLS]: {json.dumps(calls_summary)}")
            if content:
                prompt_lines.append(f"[ASSISTANT MESSAGE]: {content}")
        elif role == "tool":
            tid = msg.get("tool_call_id", "")
            tname = msg.get("name", "")
            prompt_lines.append(f"[TOOL RESULT ({tname or tid})]: {content}")
        else:
            prompt_lines.append(f"[{role.upper()}]: {content}")

    return "\n\n".join(prompt_lines)

@app.get("/v1/models")
@app.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "chatgpt-web", "object": "model", "created": int(time.time()), "owned_by": "chatgpt"},
            {"id": "gpt-6-luna", "object": "model", "created": int(time.time()), "owned_by": "chatgpt"},
            {"id": "gpt-5.6-sol", "object": "model", "created": int(time.time()), "owned_by": "chatgpt"}
        ]
    }

@app.get("/poll")
async def poll_task(url: Optional[str] = None):
    browser_status["last_seen"] = time.time()
    browser_status["tab_url"] = url
    try:
        task = await asyncio.wait_for(pending_queue.get(), timeout=25.0)
        return {"task": task}
    except asyncio.TimeoutError:
        return {"task": None}

@app.post("/reply")
async def post_reply(request: Request):
    browser_status["last_seen"] = time.time()
    payload = await request.json()
    task_id = payload.get("id")
    reply_text = payload.get("reply")
    err = payload.get("error")

    if task_id in results and not results[task_id].done():
        if err:
            results[task_id].set_exception(Exception(err))
        else:
            results[task_id].set_result(reply_text or "")
        return {"status": "ok"}
    return {"status": "unknown"}

@app.get("/status")
async def get_status():
    alive = (time.time() - browser_status["last_seen"]) < 40
    return {"status": "online" if alive else "offline", "tab_url": browser_status["tab_url"]}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    if (time.time() - browser_status["last_seen"]) >= 40:
        raise HTTPException(status_code=503, detail="Browser tab ChatGPT belum terhubung atau offline!")

    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools")
    model = body.get("model", "chatgpt-web")
    is_stream = bool(body.get("stream", False))

    prompt = format_messages_with_tools(messages, tools)

    task_id = str(uuid.uuid4())
    fut = asyncio.get_running_loop().create_future()
    results[task_id] = fut
    await pending_queue.put({"id": task_id, "prompt": prompt, "model": model})

    try:
        reply_content = await asyncio.wait_for(fut, timeout=240.0)
    finally:
        results.pop(task_id, None)

    # Deteksi apakah jawaban merupakan tool calling
    parsed_calls = parse_tool_calls_from_text(reply_content, tools) if tools else None

    if parsed_calls:
        formatted_calls = []
        for tc in parsed_calls:
            cid = f"call_{uuid.uuid4().hex[:12]}"
            args_str = json.dumps(tc["arguments"]) if isinstance(tc["arguments"], (dict, list)) else str(tc["arguments"])
            formatted_calls.append({
                "id": cid,
                "type": "function",
                "function": {
                    "name": str(tc["name"]),
                    "arguments": args_str
                }
            })

        if is_stream:
            async def sse_tools():
                yield f"data: {json.dumps({'id': f'chatcmpl-{task_id[:8]}', 'object': 'chat.completion.chunk', 'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'id': f'chatcmpl-{task_id[:8]}', 'object': 'chat.completion.chunk', 'choices': [{'delta': {'tool_calls': formatted_calls}, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'id': f'chatcmpl-{task_id[:8]}', 'object': 'chat.completion.chunk', 'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(sse_tools(), media_type="text/event-stream")

        return {
            "id": f"chatcmpl-{task_id[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": formatted_calls
                    },
                    "finish_reason": "tool_calls"
                }
            ]
        }

    # Respon teks biasa (tanpa tool calls)
    if is_stream:
        async def sse_text():
            yield f"data: {json.dumps({'id': f'chatcmpl-{task_id[:8]}', 'object': 'chat.completion.chunk', 'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
            yield f"data: {json.dumps({'id': f'chatcmpl-{task_id[:8]}', 'object': 'chat.completion.chunk', 'choices': [{'delta': {'content': reply_content}, 'finish_reason': None}]})}\n\n"
            yield f"data: {json.dumps({'id': f'chatcmpl-{task_id[:8]}', 'object': 'chat.completion.chunk', 'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(sse_text(), media_type="text/event-stream")

    return {
        "id": f"chatcmpl-{task_id[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": reply_content
                },
                "finish_reason": "stop"
            }
        ]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=20250)

EOF
chown "${TARGET_USER}:${TARGET_USER}" "${USER_HOME}/chatgpt_relay_server.py"

echo "=== [3/5] Menulis cdp_relay_driver.py (Chrome CDP Automation) ==="
cat << 'EOF' > "${USER_HOME}/cdp_relay_driver.py"
import json, time, asyncio, urllib.request, urllib.parse, websockets

BRIDGE = "http://127.0.0.1:20250"
CDP_LIST = "http://127.0.0.1:9222/json/list"


def get_ws_url():
    try:
        with urllib.request.urlopen(CDP_LIST, timeout=10) as r:
            for t in json.load(r):
                if t["type"] == "page" and "chatgpt.com" in t["url"]:
                    return t["webSocketDebuggerUrl"]
    except Exception:
        return None
    return None


def http_get(url, timeout=35):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def http_post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


class Tab:
    def __init__(self, ws):
        self.ws = ws
        self.mid = 0

    async def call(self, method, params=None, timeout=60):
        self.mid += 1
        cur = self.mid
        await self.ws.send(json.dumps({"id": cur, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            m = json.loads(raw)
            if m.get("id") == cur:
                return m
        raise TimeoutError(method + " timeout")

    async def ev(self, expr, timeout=60):
        m = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
            timeout,
        )
        return m.get("result", {}).get("result", {}).get("value")


JS_CLEAR = r"""
(() => {
  const i = document.querySelector('#prompt-textarea');
  if (!i) return false;
  i.focus();
  const s = window.getSelection();
  const r = document.createRange();
  r.selectNodeContents(i);
  s.removeAllRanges();
  s.addRange(r);
  document.execCommand('delete');
  return true;
})()
"""

JS_BASE = r"""
(() => document.querySelectorAll('[data-message-author-role="assistant"]').length)()
"""

JS_SEND = r"""
(() => {
  const b = document.querySelector('button[data-testid="send-button"]')
         || document.querySelector('button[aria-label="Send prompt"]')
         || document.querySelector('button[aria-label="Send message"]');
  if (b && !b.disabled) { b.click(); return 'clicked'; }
  return 'nobtn';
})()
"""

JS_WAIT = r"""
(async () => {
  const before = __BEFORE__;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let started = false;
  for (let i = 0; i < 300; i++) {
    await sleep(500);
    const stop = document.querySelector('button[aria-label="Stop"]')
              || document.querySelector('button[aria-label*="Stop" i]')
              || document.querySelector('button[data-testid*="stop" i]');
    if (stop) {
      started = true;
      continue;
    }
    if (started) {
      await sleep(1000);
      break;
    }
    const msgs = Array.from(document.querySelectorAll('[data-message-author-role="assistant"], [class*="MarkdownRoot"]')).filter(m => m.offsetParent !== null);
    if (msgs.length > before && i > 4) {
      await sleep(1500);
      const stillStop = document.querySelector('button[aria-label="Stop"]')
                     || document.querySelector('button[aria-label*="Stop" i]');
      if (!stillStop) break;
    }
  }
  await sleep(600);
  const msgs = Array.from(document.querySelectorAll('[data-message-author-role="assistant"], [class*="MarkdownRoot"]')).filter(m => m.offsetParent !== null);
  if (!msgs.length || msgs.length <= before) return { error: 'no new assistant msg' };
  const last = msgs[msgs.length - 1];
  return { reply: (last.innerText || last.textContent || '').trim() };
})()
"""

async def do_task(tab, prompt):
    # If currently in an old thread, click new chat
    url = await tab.ev("location.href", timeout=10)
    if url and "/c/" in url:
        await tab.ev("""(() => {
            const b = document.querySelector('a[href="/"]')
                   || Array.from(document.querySelectorAll('a, button')).find(el => el.innerText.trim().toLowerCase() === 'new chat');
            if (b) b.click();
        })()""")
        await asyncio.sleep(1.5)
    await tab.ev(JS_CLEAR, timeout=30)
    await asyncio.sleep(0.4)
    before = await tab.ev(JS_BASE, timeout=30)
    await tab.call("Input.insertText", {"text": prompt}, timeout=60)
    await asyncio.sleep(1.2)
    clicked = await tab.ev(JS_SEND, timeout=30)
    if clicked != "clicked":
        for phase in ("keyDown", "keyUp"):
            await tab.call(
                "Input.dispatchKeyEvent",
                {
                    "type": phase,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                },
                timeout=30,
            )
    return await tab.ev(JS_WAIT.replace("__BEFORE__", str(int(before or 0))), timeout=260)


async def run():
    print("[driver] starting", flush=True)
    while True:
        try:
            wsurl = get_ws_url()
            if not wsurl:
                print("[driver] no chatgpt tab", flush=True)
                await asyncio.sleep(5)
                continue
            async with websockets.connect(wsurl, max_size=30 * 1024 * 1024) as ws:
                tab = Tab(ws)
                print("[driver] attached", flush=True)
                while True:
                    try:
                        url = await tab.ev("location.href", timeout=20)
                    except Exception:
                        break
                    try:
                        data = http_get(
                            BRIDGE + "/poll?url=" + urllib.parse.quote(url or ""), timeout=35
                        )
                    except Exception as e:
                        print("[driver] poll err", e, flush=True)
                        await asyncio.sleep(3)
                        continue
                    task = data.get("task")
                    if not task:
                        continue
                    tid, prompt = task["id"], task["prompt"]
                    print("[driver] task", tid[:8], "len", len(prompt), flush=True)
                    try:
                        out = await do_task(tab, prompt)
                        if out and out.get("reply"):
                            http_post(BRIDGE + "/reply", {"id": tid, "reply": out["reply"]})
                            print("[driver] ok", tid[:8], len(out["reply"]), flush=True)
                        else:
                            err = (out or {}).get("error", "unknown")
                            http_post(BRIDGE + "/reply", {"id": tid, "error": err})
                            print("[driver] err", tid[:8], err, flush=True)
                    except Exception as e:
                        try:
                            http_post(BRIDGE + "/reply", {"id": tid, "error": str(e)})
                        except Exception:
                            pass
                        print("[driver] fail", e, flush=True)
        except Exception as e:
            print("[driver] outer", e, flush=True)
            await asyncio.sleep(5)


asyncio.run(run())

EOF
chown "${TARGET_USER}:${TARGET_USER}" "${USER_HOME}/cdp_relay_driver.py"

echo "=== [4/5] Menulis script start_chrome_cdp.sh ==="
mkdir -p "${USER_HOME}/bin"
cat << EOF > "${USER_HOME}/bin/start_chrome_cdp.sh"
#!/usr/bin/env bash
export DISPLAY=${DISPLAY_NUM}
exec /usr/bin/google-chrome-stable \
  --user-data-dir=${USER_HOME}/chrome-cdp-profile \
  --remote-debugging-port=${CDP_PORT} \
  --remote-allow-origins=* \
  --no-first-run --no-default-browser-check \
  --disable-session-crashed-bubble \
  https://chatgpt.com/
EOF
chmod +x "${USER_HOME}/bin/start_chrome_cdp.sh"
chown -R "${TARGET_USER}:${TARGET_USER}" "${USER_HOME}/bin"

echo "=== [5/5] Membuat Service Unit Systemd ==="

cat << EOF > /etc/systemd/system/chatgpt-relay.service
[Unit]
Description=ChatGPT Web Relay Bridge with Tool Calling (Port 20250)
After=network.target

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${USER_HOME}
ExecStart=${VENV_PYTHON} ${USER_HOME}/chatgpt_relay_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat << EOF > /etc/systemd/system/chatgpt-cdp-driver.service
[Unit]
Description=ChatGPT CDP Relay Driver
After=network.target chatgpt-relay.service

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${USER_HOME}
Environment=DISPLAY=${DISPLAY_NUM}
ExecStart=${VENV_PYTHON} -u ${USER_HOME}/cdp_relay_driver.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now chatgpt-relay.service
systemctl enable --now chatgpt-cdp-driver.service

echo ""
echo "=========================================================================="
echo " [OK] INSTALASI RELAY DENGAN TOOL CALLING SELESAI!"
echo "=========================================================================="
