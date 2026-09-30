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
        for i, tc in enumerate(parsed_calls):
            cid = f"call_{uuid.uuid4().hex[:12]}"
            args_str = json.dumps(tc["arguments"]) if isinstance(tc["arguments"], (dict, list)) else str(tc["arguments"])
            formatted_calls.append({
                "index": i,
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
