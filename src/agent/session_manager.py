"""
Session Manager for Agent System
세션 기반 워크플로우 상태 관리
"""
from typing import Dict, Optional, Any
import uuid
from datetime import datetime, timedelta
from src.agent.types import WorkflowSession, WorkflowPlan, Task


class SessionManager:
    """세션 기반 워크플로우 상태 관리"""
    
    def __init__(self):
        self.sessions: Dict[str, WorkflowSession] = {}
        self.session_timeout = timedelta(hours=1)  # 1시간 타임아웃
    
    def create_session(self, workflow: WorkflowPlan) -> str:
        """새 세션 생성"""
        session_id = str(uuid.uuid4())[:8]
        
        session = WorkflowSession(
            id=session_id,
            workflow=workflow,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            user_inputs={}
        )
        
        self.sessions[session_id] = session
        return session_id
    
    def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """세션 가져오기"""
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        
        # 타임아웃 체크
        if datetime.now() - session.last_activity > self.session_timeout:
            del self.sessions[session_id]
            return None
        
        # 마지막 활동 시간 업데이트
        session.last_activity = datetime.now()
        return session
    
    def update_session(self, session_id: str, **kwargs) -> bool:
        """세션 상태 업데이트"""
        session = self.get_session(session_id)
        if not session:
            return False
        
        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)
        
        session.last_activity = datetime.now()
        return True
    
    def add_user_input(self, session_id: str, key: str, value: Any) -> bool:
        """사용자 입력 추가"""
        session = self.get_session(session_id)
        if not session:
            return False
        
        session.user_inputs[key] = value
        session.last_activity = datetime.now()
        return True
    
    def get_user_inputs(self, session_id: str) -> Dict[str, Any]:
        """사용자 입력 가져오기"""
        session = self.get_session(session_id)
        if not session:
            return {}
        
        return session.user_inputs.copy()
    
    def delete_session(self, session_id: str) -> bool:
        """세션 삭제"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False
    
    def cleanup_expired_sessions(self):
        """만료된 세션 정리"""
        now = datetime.now()
        expired_sessions = [
            session_id for session_id, session in self.sessions.items()
            if now - session.last_activity > self.session_timeout
        ]
        
        for session_id in expired_sessions:
            del self.sessions[session_id]
        
        return len(expired_sessions)
    
    def get_active_sessions(self) -> Dict[str, WorkflowSession]:
        """활성 세션 목록"""
        self.cleanup_expired_sessions()
        return self.sessions.copy()
    
    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """세션 정보 요약"""
        session = self.get_session(session_id)
        if not session:
            return None
        
        return {
            "id": session.id,
            "workflow_id": session.workflow.id,
            "goal": session.workflow.goal,
            "total_tasks": len(session.workflow.tasks),
            "completed_tasks": len([t for t in session.workflow.tasks if t.status.value == "completed"]),
            "pending_tasks": len([t for t in session.workflow.tasks if t.status.value == "pending"]),
            "waiting_tasks": len([t for t in session.workflow.tasks if t.status.value == "waiting_for_user"]),
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
            "user_inputs_count": len(session.user_inputs)
        }