from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import uuid
from datetime import datetime

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_FOR_USER = "waiting_for_user"  # 사용자 입력 대기

class TaskType(Enum):
    ANALYSIS = "analysis"      # 질문 분석
    PLANNING = "planning"      # 계획 수립
    EXECUTION = "execution"    # 실행
    VERIFICATION = "verification"  # 검증
    CLEANUP = "cleanup"        # 정리
    USER_INPUT = "user_input"  # 사용자 입력 필요
    CONFIRMATION = "confirmation"  # 사용자 확인 필요

@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    type: TaskType = TaskType.EXECUTION
    status: TaskStatus = TaskStatus.PENDING
    dependencies: List[str] = field(default_factory=list)  # 의존 태스크 ID들
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Interactive 관련 필드
    user_prompt: Optional[str] = None  # 사용자에게 보여줄 질문/확인 메시지
    expected_inputs: List[Dict[str, Any]] = field(default_factory=list)  # 기대하는 입력 형식
    user_response: Optional[Dict[str, Any]] = None  # 사용자 응답

@dataclass
class WorkflowPlan:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    original_query: str = ""
    goal: str = ""
    tasks: List[Task] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)  # 진행 상황 저장
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    current_workflow: Optional[WorkflowPlan] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class WorkflowSession:
    """워크플로우 실행 세션"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    workflow: WorkflowPlan = field(default_factory=lambda: WorkflowPlan())
    user_inputs: Dict[str, Any] = field(default_factory=dict)  # 사용자 입력 저장
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    completed: bool = False