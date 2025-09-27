#!/usr/bin/env python3
"""
Claude Router Proxy for Ollama (GPT-OSS:20b) with full tool support
Supports:
- Single string input or Anthropic messages array
- All Claude Code tools
- Streaming response (SSE)
- Compatible with Docker + uvicorn --reload
"""
from dataclasses import dataclass, field
from enum import Enum
import os
import json
from typing import Any, Optional
import yaml
import requests
import re
import subprocess
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

# src 폴더에 있는 type.py와 util.py를 임포트합니다.
from src.type import *
from src.util import add_tool_instruction, build_message_start, convert_claude_tools_to_ollama, generate_signature, to_sse, convert_ollama_tool_call_to_claude, dict_to_ollama_tool_call

app = FastAPI()

# -----------------------------
# 1. 설정 로드
# -----------------------------
try:
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    config = {}

from src.const import DEFAULT_OLLAMA_URL, DEFAULT_MODEL_NAME, DEFAULT_HOST, DEFAULT_PORT

OLLAMA_URL = os.getenv("OLLAMA_URL", config.get('ollama_url', DEFAULT_OLLAMA_URL))
MODEL_NAME = os.getenv("MODEL_NAME", config.get('model_name', DEFAULT_MODEL_NAME))
HOST = os.getenv("PROXY_HOST", config.get('host', DEFAULT_HOST))
PORT = int(os.getenv("PROXY_PORT", config.get('port', DEFAULT_PORT)))

print(f"🔧 Claude Router Configuration:")
print(f"   OLLAMA_URL: {OLLAMA_URL}")
print(f"   MODEL_NAME: {MODEL_NAME}")
print(f"   HOST: {HOST}:{PORT}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "claude-router", "model": MODEL_NAME}

@app.get("/")
async def root():
    return {
        "service": "Claude Router",
        "status": "running",
        "endpoints": {"/v1/messages": "Anthropic Messages API", "/health": "Health check", "/clear-logs": "Clear log file"},
        "config": {"ollama_url": OLLAMA_URL, "model": MODEL_NAME}
    }

@app.post("/clear-logs")
async def clear_logs():
    """로그 파일 비우기 엔드포인트"""
    try:
        log_file = "claude-router.log"
        with open(log_file, "w") as f:
            f.truncate(0)
        return {"status": "success", "message": f"Log file {log_file} cleared"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to clear log file: {str(e)}"}


def stream_from_ollama(messages, model=MODEL_NAME, tools=None, tool_choice=None):
    payload = {
        "model": MODEL_NAME, 
        "messages": messages, 
        "stream": True,
        "options": {
            "temperature": 0.1,  # 낮은 온도로 일관된 응답
            "top_p": 0.9,
            "top_k": 10
        }
    }

    if tools:
        ollama_tools = convert_claude_tools_to_ollama(tools)
        if ollama_tools:
            add_tool_instruction(payload, ollama_tools, messages)

    try:
        start_message = Message(model=model)
        message_start_event = MessageStart(message=start_message)
        yield to_sse(event=Event.message_start.value, data=message_start_event)
        
        print("start to stream")
        print(f"🔥 Connecting to: {OLLAMA_URL}")
        print(f"🔥 Payload: {json.dumps(payload)}")
        
        try:
            with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=1200) as resp:
                print("🔥 Connected! Starting stream...")
                resp.raise_for_status()
                print("🔥 Response status OK")
                
                full_response = ""
                thinking_text = ""
                current_block_index = 0
                current_block_type = ""
                final_tool_calls = None

                for line in resp.iter_lines(decode_unicode=True):
                    if not line.strip():
                        continue
                    
                    # vLLM API는 "data: " prefix를 사용
                    if line.startswith("data: "):
                        line = line[6:]  # "data: " 제거
                    
                    if line.strip() == "[DONE]":
                        print("� Stream ended, [DONE] received")
                        break
                        
                    try:
                        data = json.loads(line.strip())
                        
                        # vLLM/OpenAI 형식에서 choices 배열 처리
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                            
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        tool_calls = delta.get("tool_calls", [])
                        finish_reason = choice.get("finish_reason")
                        
                        print(f"🔍 Received: finish_reason={finish_reason}, content='{content}', tool_calls={len(tool_calls)}")

                        if finish_reason == "stop" or finish_reason == "tool_calls":
                            print(f"🔚 Stream ended, finish_reason={finish_reason}")
                            if tool_calls:
                                print(f"🛠️  Found {len(tool_calls)} tool calls in final message")
                                final_tool_calls = tool_calls
                            break

                        if content:
                            full_response += content
                            if current_block_type != "content":
                                if current_block_type:
                                    yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=current_block_index))
                                    current_block_index += 1
                                
                                current_block_type = "content"
                                start_event = ContentBlockStart(index=current_block_index, content_block=ContentBlock(text=""))
                                yield to_sse(event=Event.content_block_start.value, data=start_event)
                            
                            delta_event = ContentBlockDelta(index=current_block_index, delta=ContentBlockDeltaDelta(text=content))
                            yield to_sse(event=Event.content_block_delta.value, data=delta_event)

                    except json.JSONDecodeError as e:
                        print(f"⚠️  JSON decode error: {e}")
                        continue
                
                if final_tool_calls:
                    print(f"🛠️  Processing {len(final_tool_calls)} tool calls at the end of stream.")

                    if current_block_type:
                        if current_block_type == "thinking":
                             signature_event = ContentBlockSignatureDelta(
                                index=current_block_index,
                                delta=ContentBlockSignatureDeltaDelta(signature=generate_signature(thinking_text))
                            )
                             yield to_sse(event=Event.content_block_delta.value, data=signature_event)

                        print(f"🔚 Sending content_block_stop for index {current_block_index}")
                        yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=current_block_index))
                        current_block_index += 1
                        print(f"🔄 Block index incremented to {current_block_index}")

                    for tool_call in final_tool_calls:
                        print(f"🔧 Converting Ollama tool call: {tool_call}")
                        
                        try:
                            # Convert dict to ToolCall dataclass first
                            ollama_tool_call = dict_to_ollama_tool_call(tool_call)
                            
                            # Convert ToolCall to ClaudeToolCall dataclass
                            claude_tool_call = convert_ollama_tool_call_to_claude(ollama_tool_call)
                            
                            # Extract data from ClaudeToolCall dataclass
                            tool_id = claude_tool_call.id
                            tool_name = claude_tool_call.name
                            validated_args = claude_tool_call.input
                            
                            print(f"  ✅ Converted to Claude format - Tool: {tool_name}, Args: {validated_args}")
                            
                        except (ValueError, TypeError) as e:
                            print(f"❌ Failed to convert tool call: {e}. Skipping.")
                            continue

                        tool_use_content_block = ContentBlockToolUse(
                            type="tool_use",
                            id=tool_id,
                            name=tool_name
                        )
                        
                        start_event = ContentBlockStart(index=current_block_index, content_block=tool_use_content_block)
                        yield to_sse(event=Event.content_block_start.value, data=start_event)
                        
                        if validated_args:
                            input_json = json.dumps(validated_args, ensure_ascii=False)
                            delta_event = ContentBlockDelta(
                                index=current_block_index,
                                delta=ContentBlockToolUseDelta(
                                    type="input_json_delta",
                                    partial_json=input_json
                                )
                            )
                            yield to_sse(event=Event.content_block_delta.value, data=delta_event)
                        
                        stop_event = ContentBlockStop(index=current_block_index)
                        yield to_sse(event=Event.content_block_stop.value, data=stop_event)
                        current_block_index += 1
                    
                    usage_info = Usage(output_tokens=len(final_tool_calls) * 10)
                    delta_info = MessageDeltaDelta(stop_reason="tool_use", stop_sequence=None)
                    stop_reason_delta = MessageDelta(delta=delta_info, usage=usage_info)
                    yield to_sse(event=Event.message_delta.value, data=stop_reason_delta)
                    
                else: 
                    if current_block_type:
                        if current_block_type == "thinking":
                             signature_event = ContentBlockSignatureDelta(
                                index=current_block_index,
                                delta=ContentBlockSignatureDeltaDelta(signature=generate_signature(thinking_text))
                            )
                             yield to_sse(event=Event.content_block_delta.value, data=signature_event)

                        yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=current_block_index))
                    
                    output_tokens = len(full_response.split()) if full_response else 0
                    usage_info = Usage(output_tokens=output_tokens)
                    message_delta = MessageDelta(usage=usage_info)
                    yield to_sse(event=Event.message_delta.value, data=message_delta)

        except requests.exceptions.Timeout as e:
            print(f"🔥 TIMEOUT: Ollama request timed out after {resp.timeout if 'resp' in locals() else 300}s")
            print(f"🔥 This usually means the model is thinking too long or got stuck")
            error_event = Error(error=ErrorMessage(message=f"Request timeout: {str(e)}"))
            yield to_sse(event=Event.error.value, data=error_event)
            return
        except requests.exceptions.RequestException as e:
            print(f"🔥 Connection failed: {e}")
            error_event = Error(error=ErrorMessage(message=str(e)))
            yield to_sse(event=Event.error.value, data=error_event)
            return

        message_stop = MessageStop()
        yield to_sse(event=Event.message_stop.value, data=message_stop)
    
    except Exception as e:
        print(f"🔥 Unexpected error: {e}")
        error_event = Error(error=ErrorMessage(message=str(e)))
        yield to_sse(event=Event.error.value, data=error_event)

@app.post("/v1/messages")
async def messages_endpoint(request: Request):
    payload = await request.json()
    print(f"Received payload: {json.dumps(payload)}")
    
    messages = payload.get("messages") or payload.get("input") or ""
    model = MODEL_NAME
    tools = payload.get("tools")
    tool_choice = payload.get("tool_choice")
    
    # Convert Anthropic messages format to Ollama format
    if messages and isinstance(messages, list):
        from src.util import convert_messages_to_ollama_format
        messages = convert_messages_to_ollama_format(messages)
    
    return StreamingResponse(
        stream_from_ollama(messages, model, tools, tool_choice),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Access-Control-Allow-Origin": "*"}
    )

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 Starting Claude Router on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)