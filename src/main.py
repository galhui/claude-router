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
import asyncio

# Agent 시스템 import
from src.agent.analyzer import QueryAnalyzer
from src.agent.executor import WorkflowExecutor

# src 폴더에 있는 type.py와 util.py를 임포트합니다.
from src.type import *
from src.util import add_tool_instruction, build_message_start, convert_claude_tools_to_ollama, generate_signature, to_sse, convert_ollama_tool_call_to_claude, dict_to_ollama_tool_call
from src.agent.analyzer import QueryAnalyzer
from src.agent.executor import WorkflowExecutor

app = FastAPI()

# Agent 시스템 초기화 
query_analyzer = QueryAnalyzer()
workflow_executor = None  # 서버 시작 후 초기화

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

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 Agent 시스템 초기화"""
    global workflow_executor
    
    # 간단한 클라이언트 객체 생성 (requests 사용)
    class SimpleOllamaClient:
        def __init__(self, base_url):
            self.base_url = base_url.rstrip('/')
        
        def post(self, endpoint, **kwargs):
            return requests.post(f"{self.base_url}{endpoint}", **kwargs)
    
    ollama_client = SimpleOllamaClient(OLLAMA_URL)
    workflow_executor = WorkflowExecutor(ollama_client=ollama_client)
    print("🤖 Agent 시스템 초기화 완료")
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

@app.post("/v1/agent/analyze")
async def analyze_query(request: Request):
    """질문 분석 및 워크플로우 계획 생성"""
    payload = await request.json()
    query = payload.get("query", "")
    
    if not query:
        return {"error": "질문이 필요합니다"}
    
    try:
        plan = query_analyzer.analyze_query(query)
        return {
            "workflow_id": plan.id,
            "goal": plan.goal,
            "total_tasks": len(plan.tasks),
            "tasks": [{
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "type": task.type.value,
                "status": task.status.value
            } for task in plan.tasks]
        }
    except Exception as e:
        return {"error": f"분석 실패: {str(e)}"}

@app.post("/v1/agent/execute")
async def execute_workflow(request: Request):
    """워크플로우 실행"""
    payload = await request.json()
    query = payload.get("query", "")
    session_id = payload.get("session_id")
    
    if not query:
        return {"error": "질문이 필요합니다"}
    
    try:
        # 1. 질문 분석
        plan = query_analyzer.analyze_query(query)
        print(f"🎯 실행 계획: {plan.goal}")
        print(f"📋 총 {len(plan.tasks)}개 태스크")
        
        # 2. 워크플로우 실행
        result = await workflow_executor.execute_workflow(plan, session_id)
        return result
        
    except Exception as e:
        return {"error": f"실행 실패: {str(e)}"}

@app.get("/v1/agent/status/{workflow_id}")
async def get_workflow_status(workflow_id: str, session_id: str):
    """워크플로우 진행 상황 조회"""
    return workflow_executor.get_workflow_status(workflow_id, session_id)

@app.post("/v1/agent/respond")
async def respond_to_task(request: Request):
    """사용자 입력이 필요한 태스크에 응답"""
    payload = await request.json()
    session_id = payload.get("session_id")
    task_id = payload.get("task_id")
    user_response = payload.get("response", {})
    
    if not session_id or not task_id:
        return {"error": "session_id와 task_id가 필요합니다"}
    
    try:
        # 세션에서 해당 태스크 찾기
        if session_id not in workflow_executor.sessions:
            return {"error": "세션을 찾을 수 없습니다"}
        
        session = workflow_executor.sessions[session_id]
        workflow = session.current_workflow
        
        if not workflow:
            return {"error": "활성 워크플로우가 없습니다"}
        
        # 태스크 찾기
        task = None
        for t in workflow.tasks:
            if t.id == task_id:
                task = t
                break
        
        if not task:
            return {"error": "태스크를 찾을 수 없습니다"}
        
        if task.status != TaskStatus.WAITING_FOR_USER:
            return {"error": "이 태스크는 사용자 입력을 기다리고 있지 않습니다"}
        
        # 사용자 응답 저장
        task.user_response = user_response
        task.status = TaskStatus.PENDING  # 다시 실행 대기 상태로
        
        print(f"📥 사용자 응답 수신: Task {task_id} - {user_response}")
        
        # 워크플로우 재개
        result = await workflow_executor.resume_workflow(workflow.id, session_id)
        
        return {
            "message": "사용자 응답이 처리되었습니다",
            "task_id": task_id,
            "workflow_result": result
        }
        
    except Exception as e:
        return {"error": f"응답 처리 실패: {str(e)}"}

@app.post("/v1/agent/skip")  
async def skip_task(request: Request):
    """사용자 입력 태스크 건너뛰기"""
    payload = await request.json()
    session_id = payload.get("session_id")
    task_id = payload.get("task_id")
    
    if not session_id or not task_id:
        return {"error": "session_id와 task_id가 필요합니다"}
    
    try:
        session = workflow_executor.sessions[session_id]
        workflow = session.current_workflow
        
        task = None
        for t in workflow.tasks:
            if t.id == task_id:
                task = t
                break
        
        if task:
            task.status = TaskStatus.SKIPPED
            print(f"⏭️  태스크 건너뛰기: {task.title}")
            
            # 워크플로우 재개
            result = await workflow_executor.resume_workflow(workflow.id, session_id)
            return {"message": "태스크가 건너뛰어졌습니다", "workflow_result": result}
        else:
            return {"error": "태스크를 찾을 수 없습니다"}
            
    except Exception as e:
        return {"error": f"건너뛰기 실패: {str(e)}"}


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
                    if not line or not line.strip():
                        continue
                    
                    # Ensure line is a string
                    if isinstance(line, bytes):
                        line = line.decode('utf-8')
                    
                    # Ollama API는 직접 JSON을 반환 (data: prefix 없음)
                    line = line.strip()
                    
                    if line.strip() == "[DONE]":
                        print("� Stream ended, [DONE] received")
                        break
                        
                    try:
                        data = json.loads(line.strip())
                        
                        # Ollama 형식: {"message": {"content": "text"}, "done": false}
                        message = data.get("message", {})
                        content = message.get("content", "")
                        thinking = message.get("thinking", "")
                        done = data.get("done", False)
                        
                        print(f"🔍 Received: done={done}, content='{content}', thinking='{thinking}'")

                        if done:
                            print(f"🔚 Stream ended, done={done}")
                            break

                        if content:
                            full_response += content
                            if current_block_type != "content":
                                if current_block_type == "thinking":
                                    # thinking에서 content로 전환 시 thinking 블록 종료 + signature
                                    if thinking_text:
                                        signature_event = ContentBlockSignatureDelta(
                                            index=current_block_index,
                                            delta=ContentBlockSignatureDeltaDelta(signature=generate_signature(thinking_text))
                                        )
                                        yield to_sse(event=Event.content_block_delta.value, data=signature_event)
                                    
                                    yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=current_block_index))
                                    current_block_index += 1
                                    print(f"🧠 Ended thinking block, starting content block at index {current_block_index}")
                                
                                current_block_type = "content"
                                content_block = ContentBlock(text="")
                                start_event = ContentBlockStart(index=current_block_index, content_block=content_block)
                                yield to_sse(event=Event.content_block_start.value, data=start_event)
                            
                            # content delta 전송
                            text_delta = ContentBlockDeltaDelta(text=content)
                            delta_event = ContentBlockDelta(index=current_block_index, delta=text_delta)
                            yield to_sse(event=Event.content_block_delta.value, data=delta_event)
                            print(f"💬 Content: {content}")
                        
                        if thinking:
                            thinking_text += thinking
                            if current_block_type != "thinking":
                                if current_block_type == "content":
                                    # content 블록이 있었다면 먼저 종료
                                    yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=current_block_index))
                                    current_block_index += 1
                                
                                current_block_type = "thinking"
                                thinking_block = ContentBlockThinking()
                                start_event = ContentBlockStart(index=current_block_index, content_block=thinking_block)
                                yield to_sse(event=Event.content_block_start.value, data=start_event)
                                print(f"🧠 Started thinking block at index {current_block_index}")
                            
                            # thinking delta 전송
                            thinking_delta = ContentBlockThinkingDeltaDelta(thinking=thinking)
                            delta_event = ContentBlockDelta(index=current_block_index, delta=thinking_delta)
                            yield to_sse(event=Event.content_block_delta.value, data=delta_event)
                            print(f"🧠 Thinking: {thinking}")

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
    
    # 최신 사용자 메시지 추출
    user_message = ""
    if messages and isinstance(messages, list):
        for msg in reversed(messages):  # 뒤에서부터 찾기
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    user_message = content
                elif isinstance(content, list):
                    # content가 배열인 경우 텍스트 부분만 추출하되 system-reminder는 제외
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_content = item.get("text", "")
                            # system-reminder 메시지 제외
                            if not text_content.startswith("<system-reminder>"):
                                user_message = text_content
                                if user_message:  # 첫 번째 유효한 사용자 메시지 찾으면 종료
                                    break
                break
    
    # Agent 시스템 트리거 조건 확인
    should_use_agent = False
    agent_keywords = [
        "만들어", "생성", "create", "프로젝트", "project", 
        "설치", "install", "설정", "config", "초기화", "init",
        "nestjs", "react", "vue", "node", "python", "django", "flask"
    ]
    
    if user_message:
        user_message_lower = user_message.lower()
        should_use_agent = any(keyword in user_message_lower for keyword in agent_keywords)
        print(f"🤖 User message: {user_message[:100]}...")
        print(f"🤖 Should use agent: {should_use_agent}")
    
    # Agent 시스템 사용
    if should_use_agent and user_message:
        print("🚀 Agent 시스템으로 처리 중...")
        try:
            # 1. 질문 분석
            plan = query_analyzer.analyze_query(user_message)
            print(f"🎯 실행 계획: {plan.goal}")
            print(f"📋 총 {len(plan.tasks)}개 태스크")
            
            # 2. 워크플로우 실행 시작
            async def agent_stream():
                try:
                    # Message start 이벤트
                    start_message = Message(model=model)
                    message_start_event = MessageStart(message=start_message)
                    yield to_sse(event=Event.message_start.value, data=message_start_event)
                    
                    # Thinking 블록 시작
                    thinking_block = ContentBlockThinking()
                    thinking_start = ContentBlockStart(index=0, content_block=thinking_block)
                    yield to_sse(event=Event.content_block_start.value, data=thinking_start)
                    
                    # 계획 설명
                    thinking_text = f"사용자가 '{user_message}'를 요청했습니다. 이를 {len(plan.tasks)}개의 단계로 나누어 처리하겠습니다:\n"
                    for i, task in enumerate(plan.tasks, 1):
                        thinking_text += f"{i}. {task.title}: {task.description}\n"
                    
                    # Thinking 내용 스트리밍
                    for char in thinking_text:
                        thinking_delta = ContentBlockThinkingDeltaDelta(thinking=char)
                        delta_event = ContentBlockDelta(index=0, delta=thinking_delta)
                        yield to_sse(event=Event.content_block_delta.value, data=delta_event)
                        await asyncio.sleep(0.01)  # 자연스러운 타이핑 효과
                    
                    # Thinking 종료
                    signature_event = ContentBlockSignatureDelta(
                        index=0,
                        delta=ContentBlockSignatureDeltaDelta(signature=generate_signature(thinking_text))
                    )
                    yield to_sse(event=Event.content_block_delta.value, data=signature_event)
                    yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=0))
                    
                    # 워크플로우 실행
                    print("🚀 워크플로우 실행 시작...")
                    result = await workflow_executor.execute_workflow(plan)
                    print(f"🎯 워크플로우 실행 결과: {result}")
                    
                    # 결과를 content 블록으로 스트리밍
                    content_block = ContentBlock(text="")
                    content_start = ContentBlockStart(index=1, content_block=content_block)
                    yield to_sse(event=Event.content_block_start.value, data=content_start)
                    
                    # 결과 메시지 생성
                    if result["status"] == "waiting_for_user":
                        response_text = f"✅ 계획이 수립되었습니다!\n\n📋 **{plan.goal}** 작업을 진행하겠습니다.\n\n"
                        
                        # 대기 중인 태스크 정보 표시
                        waiting_tasks = [t for t in plan.tasks if t.status.value == "waiting_for_user"]
                        if waiting_tasks:
                            task = waiting_tasks[0]
                            response_text += f"⏳ **다음 단계**: {task.title}\n\n"
                            if task.user_prompt:
                                response_text += f"💬 {task.user_prompt}\n\n"
                            
                            if task.expected_inputs:
                                response_text += "📝 **설정이 필요한 항목들**:\n"
                                for inp in task.expected_inputs:
                                    response_text += f"- **{inp['description']}**: "
                                    if inp.get('options'):
                                        response_text += f"({', '.join(inp['options'])})"
                                    if inp.get('default'):
                                        response_text += f" [기본값: {inp['default']}]"
                                    response_text += "\n"
                        
                        response_text += f"\n🔗 세션 ID: `{result.get('session_id')}`\n"
                        response_text += f"🆔 워크플로우 ID: `{result.get('workflow_id')}`\n"
                        
                    elif result["status"] == "completed":
                        response_text = f"🎉 **{plan.goal}** 작업이 완료되었습니다!\n\n"
                        response_text += f"✅ 총 {result['completed_tasks']}/{result['total_tasks']}개 태스크 완료\n\n"
                        
                        # 실행된 작업들 요약
                        for task_result in result.get('results', []):
                            if task_result['status'] == 'completed':
                                response_text += f"✅ {task_result['title']}\n"
                            else:
                                response_text += f"❌ {task_result['title']}: {task_result.get('error', '실패')}\n"
                    
                    else:
                        response_text = f"❌ 작업 중 오류가 발생했습니다: {result.get('error', '알 수 없는 오류')}"
                    
                    # 응답 텍스트 스트리밍
                    for char in response_text:
                        text_delta = ContentBlockDeltaDelta(text=char)
                        delta_event = ContentBlockDelta(index=1, delta=text_delta)
                        yield to_sse(event=Event.content_block_delta.value, data=delta_event)
                        await asyncio.sleep(0.005)
                    
                    yield to_sse(event=Event.content_block_stop.value, data=ContentBlockStop(index=1))
                    
                    # Message delta와 stop
                    usage_info = Usage(output_tokens=len(response_text.split()))
                    message_delta = MessageDelta(usage=usage_info)
                    yield to_sse(event=Event.message_delta.value, data=message_delta)
                    
                    message_stop = MessageStop()
                    yield to_sse(event=Event.message_stop.value, data=message_stop)
                    
                except Exception as e:
                    print(f"❌ Agent 처리 오류: {e}")
                    error_event = Error(error=ErrorMessage(message=str(e)))
                    yield to_sse(event=Event.error.value, data=error_event)
            
            return StreamingResponse(
                agent_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Access-Control-Allow-Origin": "*"}
            )
            
        except Exception as e:
            print(f"❌ Agent 시스템 오류: {e}")
            # 오류 시 일반 Claude 대화로 폴백
    
    # 일반 Claude 대화 처리
    print("💬 일반 Claude 대화로 처리 중...")
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