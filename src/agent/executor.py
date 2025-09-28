import asyncio
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

from .types import WorkflowPlan, Task, TaskStatus, SessionState
from ..util import convert_claude_tools_to_ollama

class WorkflowExecutor:
    """워크플로우 단계별 실행 및 상태 관리"""
    
    def __init__(self, ollama_client):
        self.ollama_client = ollama_client
        self.sessions: Dict[str, SessionState] = {}
    
    def get_or_create_session(self, session_id: Optional[str] = None) -> SessionState:
        """세션 가져오기 또는 생성"""
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        
        session = SessionState(session_id=session_id or SessionState().session_id)
        self.sessions[session.session_id] = session
        return session
    
    async def execute_workflow(self, plan: WorkflowPlan, session_id: Optional[str] = None) -> Dict[str, Any]:
        """워크플로우 전체 실행"""
        session = self.get_or_create_session(session_id)
        session.current_workflow = plan
        
        plan.status = TaskStatus.IN_PROGRESS
        
        try:
            # 의존성 순서에 따라 태스크 정렬
            sorted_tasks = self._topological_sort(plan.tasks)
            
            results = []
            for task in sorted_tasks:
                if task.status == TaskStatus.COMPLETED:
                    continue  # 이미 완료된 태스크는 스킵
                
                print(f"🔄 실행 중: {task.title}")
                result = await self._execute_task(task, session)
                results.append(result)
                
                # 사용자 입력 대기나 실패 시 워크플로우 중단
                if task.status == TaskStatus.WAITING_FOR_USER:
                    plan.status = TaskStatus.WAITING_FOR_USER
                    print(f"⏸️  사용자 입력 대기로 워크플로우 일시 중지: {task.title}")
                    break
                elif task.status == TaskStatus.FAILED:
                    plan.status = TaskStatus.FAILED
                    print(f"❌ 태스크 실패로 워크플로우 중단: {task.title}")
                    break
            
            if plan.status != TaskStatus.FAILED:
                plan.status = TaskStatus.COMPLETED
            
            return {
                "workflow_id": plan.id,
                "status": plan.status.value,
                "completed_tasks": len([t for t in plan.tasks if t.status == TaskStatus.COMPLETED]),
                "total_tasks": len(plan.tasks),
                "results": results,
                "session_id": session.session_id
            }
            
        except Exception as e:
            plan.status = TaskStatus.FAILED
            print(f"❌ 워크플로우 실행 실패: {e}")
            return {
                "workflow_id": plan.id,
                "status": "failed",
                "error": str(e),
                "session_id": session.session_id
            }
    
    async def _execute_task(self, task: Task, session: SessionState) -> Dict[str, Any]:
        """개별 태스크 실행"""
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = datetime.now()
        
        try:
            # 태스크 유형별 실행
            if task.type.value == "analysis":
                result = await self._execute_analysis_task(task, session)
            elif task.type.value == "user_input" or task.type.value == "confirmation":
                result = await self._execute_interactive_task(task, session)
            elif task.type.value == "execution":
                result = await self._execute_execution_task(task, session)
            elif task.type.value == "verification":
                result = await self._execute_verification_task(task, session)
            else:
                result = {"message": f"태스크 타입 {task.type.value} 실행됨"}
            
            task.status = TaskStatus.COMPLETED
            task.result = json.dumps(result, ensure_ascii=False)
            task.completed_at = datetime.now()
            
            print(f"✅ 완료: {task.title}")
            return {
                "task_id": task.id,
                "title": task.title,
                "status": "completed",
                "result": result
            }
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            
            print(f"❌ 실패: {task.title} - {e}")
            return {
                "task_id": task.id,
                "title": task.title,
                "status": "failed",
                "error": str(e)
            }
    
    async def _execute_interactive_task(self, task: Task, session: SessionState) -> Dict[str, Any]:
        """사용자 입력/확인이 필요한 태스크 실행"""
        # 사용자 입력이 이미 있는 경우 처리
        if task.user_response:
            print(f"✅ 사용자 응답 처리: {task.user_response}")
            return {
                "type": "interactive",
                "user_input_received": True,
                "response": task.user_response
            }
        
        # 사용자 입력 대기
        task.status = TaskStatus.WAITING_FOR_USER
        print(f"⏳ 사용자 입력 대기: {task.title}")
        
        return {
            "type": "interactive",
            "waiting_for_user": True,
            "prompt": task.user_prompt,
            "expected_inputs": task.expected_inputs,
            "task_id": task.id
        }
    
    async def _execute_analysis_task(self, task: Task, session: SessionState) -> Dict[str, Any]:
        """분석 태스크 실행"""
        return {
            "type": "analysis",
            "description": task.description,
            "completed": True
        }
    
    async def _execute_execution_task(self, task: Task, session: SessionState) -> Dict[str, Any]:
        """실행 태스크 실행 (도구 호출)"""
        results = []
        
        # 워크플로우의 모든 태스크에서 사용자 응답 수집
        template_vars = {}
        workflow = session.current_workflow
        for prev_task in workflow.tasks:
            if prev_task.user_response:
                template_vars.update(prev_task.user_response)
                print(f"📝 템플릿 변수 추가: {prev_task.user_response}")
        
        for tool_call in task.tool_calls:
            tool_name = tool_call.get("tool")
            arguments = tool_call.get("arguments", {})
            
            # 템플릿 변수 교체
            processed_arguments = self._replace_template_variables(arguments, template_vars)
            
            print(f"🛠️  도구 호출: {tool_name}")
            print(f"   원본: {arguments}")
            print(f"   처리됨: {processed_arguments}")
            
            # 도구 실행
            if tool_name == "Bash":
                import subprocess
                try:
                    command = processed_arguments.get("command", "")
                    print(f"💻 실행 명령어: {command}")
                    
                    result = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    results.append({
                        "tool": tool_name,
                        "command": command,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "returncode": result.returncode,
                        "success": result.returncode == 0
                    })
                    
                    if result.returncode == 0:
                        print(f"✅ 명령어 성공 실행")
                    else:
                        print(f"❌ 명령어 실패: {result.stderr}")
                        
                except subprocess.TimeoutExpired:
                    print(f"⏱️  명령어 시간 초과")
                    results.append({
                        "tool": tool_name,
                        "command": command,
                        "error": "Timeout",
                        "success": False
                    })
                except Exception as e:
                    print(f"❌ 명령어 실행 오류: {e}")
                    results.append({
                        "tool": tool_name,
                        "command": command,
                        "error": str(e),
                        "success": False
                    })
            else:
                # 다른 도구들은 Ollama를 통해 처리
                results.append({
                    "tool": tool_name,
                    "arguments": processed_arguments,
                    "note": "도구 실행 로직 구현 필요",
                    "success": True
                })
        
        return {
            "type": "execution",
            "tool_results": results,
            "template_vars_used": template_vars
        }
    
    def _replace_template_variables(self, arguments: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
        """템플릿 변수를 실제 값으로 교체"""
        import re
        
        def replace_in_string(text: str) -> str:
            if not isinstance(text, str):
                return text
            
            # {{variable_name}} 형태의 변수를 찾아서 교체
            pattern = r'\{\{(\w+)\}\}'
            
            def replacer(match):
                var_name = match.group(1)
                return str(variables.get(var_name, match.group(0)))  # 변수가 없으면 원문 유지
            
            return re.sub(pattern, replacer, text)
        
        # arguments의 모든 문자열 값에 대해 템플릿 변수 교체
        processed = {}
        for key, value in arguments.items():
            if isinstance(value, str):
                processed[key] = replace_in_string(value)
            elif isinstance(value, dict):
                processed[key] = self._replace_template_variables(value, variables)
            elif isinstance(value, list):
                processed[key] = [
                    replace_in_string(item) if isinstance(item, str) else item 
                    for item in value
                ]
            else:
                processed[key] = value
        
        return processed
    
    async def _execute_verification_task(self, task: Task, session: SessionState) -> Dict[str, Any]:
        """검증 태스크 실행"""
        # 의존성 태스크들의 결과 확인
        workflow = session.current_workflow
        dependent_tasks = [t for t in workflow.tasks if t.id in task.dependencies]
        
        failed_tasks = [t for t in dependent_tasks if t.status == TaskStatus.FAILED]
        if failed_tasks:
            raise Exception(f"의존 태스크 실패: {[t.title for t in failed_tasks]}")
        
        return {
            "type": "verification",
            "verified_tasks": [t.title for t in dependent_tasks],
            "all_passed": len(failed_tasks) == 0
        }
    
    def _topological_sort(self, tasks: List[Task]) -> List[Task]:
        """의존성 순서에 따른 태스크 정렬"""
        # 간단한 위상 정렬 구현
        task_dict = {task.id: task for task in tasks}
        result = []
        visited = set()
        
        def visit(task_id: str):
            if task_id in visited:
                return
            
            task = task_dict[task_id]
            for dep_id in task.dependencies:
                if dep_id in task_dict:
                    visit(dep_id)
            
            visited.add(task_id)
            result.append(task)
        
        for task in tasks:
            visit(task.id)
        
        return result
    
    def get_workflow_status(self, workflow_id: str, session_id: str) -> Dict[str, Any]:
        """워크플로우 진행 상황 조회"""
        if session_id not in self.sessions:
            return {"error": "세션을 찾을 수 없습니다"}
        
        session = self.sessions[session_id]
        workflow = session.current_workflow
        
        if not workflow or workflow.id != workflow_id:
            return {"error": "워크플로우를 찾을 수 없습니다"}
        
        return {
            "workflow_id": workflow.id,
            "status": workflow.status.value,
            "goal": workflow.goal,
            "tasks": [{
                "id": task.id,
                "title": task.title,
                "type": task.type.value,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                # Interactive 관련 정보
                "user_prompt": task.user_prompt if task.status == TaskStatus.WAITING_FOR_USER else None,
                "expected_inputs": task.expected_inputs if task.status == TaskStatus.WAITING_FOR_USER else None
            } for task in workflow.tasks]
        }
    
    async def resume_workflow(self, workflow_id: str, session_id: str) -> Dict[str, Any]:
        """일시 중지된 워크플로우 재개"""
        if session_id not in self.sessions:
            return {"error": "세션을 찾을 수 없습니다"}
        
        session = self.sessions[session_id]
        workflow = session.current_workflow
        
        if not workflow or workflow.id != workflow_id:
            return {"error": "워크플로우를 찾을 수 없습니다"}
        
        if workflow.status != TaskStatus.WAITING_FOR_USER:
            return {"error": "재개할 수 있는 워크플로우가 아닙니다"}
        
        print(f"🔄 워크플로우 재개: {workflow.goal}")
        
        # 워크플로우 상태를 다시 진행 중으로 변경
        workflow.status = TaskStatus.IN_PROGRESS
        
        # 나머지 태스크들 실행
        sorted_tasks = self._topological_sort(workflow.tasks)
        results = []
        
        for task in sorted_tasks:
            # 이미 완료되거나 건너뛴 태스크는 제외
            if task.status in [TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.FAILED]:
                continue
                
            print(f"🔄 실행 중: {task.title}")
            result = await self._execute_task(task, session)
            results.append(result)
            
            # 사용자 입력 대기나 실패 시 중단
            if task.status in [TaskStatus.WAITING_FOR_USER, TaskStatus.FAILED]:
                workflow.status = task.status
                break
        
        if workflow.status == TaskStatus.IN_PROGRESS:
            workflow.status = TaskStatus.COMPLETED
        
        return {
            "workflow_id": workflow.id,
            "status": workflow.status.value,
            "resumed_tasks": len(results),
            "results": results
        }