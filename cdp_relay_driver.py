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
