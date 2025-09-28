import re
import json
from typing import List, Dict, Any
from .types import WorkflowPlan, Task, TaskType, TaskStatus

class QueryAnalyzer:
    """사용자 질문을 분석하여 실행 가능한 단계들로 분해"""
    
    def __init__(self):
        self.patterns = {
            # 프로젝트 생성 패턴
            'create_project': [
                r'(?:만들어|생성|create).*(?:프로젝트|project)',
                r'(?:nestjs|react|vue|node\.?js|python|django|flask).*(?:프로젝트|project)',
                r'(?:백엔드|프론트엔드|full-?stack).*(?:프로젝트|project)'
            ],
            # 파일 조작 패턴
            'file_operations': [
                r'(?:파일|file).*(?:만들어|생성|create|write)',
                r'(?:코드|code).*(?:작성|write)',
                r'(?:설정|config).*(?:파일|file)'
            ],
            # 설치/설정 패턴
            'installation': [
                r'(?:설치|install).*(?:패키지|package|라이브러리|library)',
                r'(?:npm|pip|yarn).*(?:install)',
                r'(?:dependencies|의존성).*(?:설치|install)'
            ],
            # 실행/테스트 패턴
            'execution': [
                r'(?:실행|run|start)',
                r'(?:테스트|test)',
                r'(?:빌드|build)',
                r'(?:배포|deploy)'
            ]
        }
    
    def analyze_query(self, query: str) -> WorkflowPlan:
        """질문을 분석하여 워크플로우 계획 생성"""
        plan = WorkflowPlan(
            original_query=query,
            goal=self._extract_goal(query)
        )
        
        # 1. 질문 유형 판별
        query_types = self._classify_query(query)
        
        # 2. 기본 분석 태스크 추가
        analysis_task = Task(
            title="질문 분석",
            description=f"'{query}' 요청 분석 및 목표 설정",
            type=TaskType.ANALYSIS,
            status=TaskStatus.COMPLETED  # 이미 완료됨
        )
        plan.tasks.append(analysis_task)
        
        # 3. 유형별 태스크 생성
        if 'create_project' in query_types:
            self._add_project_creation_tasks(plan, query)
        
        if 'file_operations' in query_types:
            self._add_file_operation_tasks(plan, query)
            
        if 'installation' in query_types:
            self._add_installation_tasks(plan, query)
            
        if 'execution' in query_types:
            self._add_execution_tasks(plan, query)
        
        # 4. 검증 태스크 추가
        if len(plan.tasks) > 1:  # analysis 외에 다른 태스크가 있으면
            verification_task = Task(
                title="결과 검증",
                description="모든 작업이 올바르게 완료되었는지 확인",
                type=TaskType.VERIFICATION,
                dependencies=[task.id for task in plan.tasks if task.type == TaskType.EXECUTION]
            )
            plan.tasks.append(verification_task)
        
        return plan
    
    def _extract_goal(self, query: str) -> str:
        """질문에서 목표 추출"""
        # 간단한 목표 추출 로직
        if 'nestjs' in query.lower():
            return "NestJS 프로젝트 생성"
        elif 'react' in query.lower():
            return "React 프로젝트 생성"
        elif '프로젝트' in query:
            return "프로젝트 생성"
        elif '파일' in query:
            return "파일 작업"
        else:
            return "사용자 요청 처리"
    
    def _classify_query(self, query: str) -> List[str]:
        """질문을 유형별로 분류"""
        query_lower = query.lower()
        matched_types = []
        
        for query_type, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    matched_types.append(query_type)
                    break
        
        return matched_types
    
    def _add_project_creation_tasks(self, plan: WorkflowPlan, query: str):
        """프로젝트 생성 태스크들 추가"""
        # 프로젝트명 추출
        project_name = self._extract_project_name(query)
        
        # 간단한 실행 태스크들로 구성 (사용자 입력 없이)
        
        # 1. 분석 태스크 (기본 정보 설정)
        analysis_task = Task(
            title="질문 분석",
            description=f"'{query}' 요청 분석 및 목표 설정",
            type=TaskType.ANALYSIS
        )
        plan.tasks.append(analysis_task)
        
        # 2. 디렉토리 생성
        dir_task = Task(
            title="프로젝트 디렉토리 생성",
            description="프로젝트 디렉토리를 생성합니다",
            type=TaskType.EXECUTION,
            dependencies=[analysis_task.id],
            tool_calls=[{
                "tool": "Bash",
                "arguments": {
                    "command": f"mkdir -p {project_name}",
                    "description": "프로젝트 디렉토리 생성"
                }
            }]
        )
        plan.tasks.append(dir_task)
        
        # NestJS 프로젝트인 경우
        if 'nestjs' in query.lower():
            # 3. NestJS CLI 설치 확인
            cli_task = Task(
                title="NestJS CLI 확인",
                description="NestJS CLI가 설치되어 있는지 확인하고 필요시 설치합니다",
                type=TaskType.EXECUTION,
                dependencies=[dir_task.id],
                tool_calls=[{
                    "tool": "Bash",
                    "arguments": {
                        "command": "npm list -g @nestjs/cli || npm install -g @nestjs/cli",
                        "description": "NestJS CLI 설치 확인 및 설치"
                    }
                }]
            )
            plan.tasks.append(cli_task)
            
            # 4. NestJS 프로젝트 생성
            create_task = Task(
                title="NestJS 프로젝트 초기화",
                description="NestJS CLI로 프로젝트를 생성합니다",
                type=TaskType.EXECUTION,
                dependencies=[cli_task.id],
                tool_calls=[{
                    "tool": "Bash",
                    "arguments": {
                        "command": f"nest new {project_name} --package-manager npm --skip-git",
                        "description": "NestJS 프로젝트 생성"
                    }
                }]
            )
            plan.tasks.append(create_task)
            
            # 5. 의존성 설치 확인
            deps_task = Task(
                title="의존성 설치",
                description="프로젝트 의존성 패키지를 설치합니다",
                type=TaskType.EXECUTION,
                dependencies=[create_task.id],
                tool_calls=[{
                    "tool": "Bash",
                    "arguments": {
                        "command": f"cd {project_name} && npm install",
                        "description": "의존성 설치"
                    }
                }]
            )
            plan.tasks.append(deps_task)
            
            # 6. 생성 확인
            verify_task = Task(
                title="프로젝트 생성 완료 확인",
                description="프로젝트가 정상적으로 생성되었는지 확인합니다",
                type=TaskType.VERIFICATION,
                dependencies=[deps_task.id],
                tool_calls=[{
                    "tool": "Bash",
                    "arguments": {
                        "command": f"ls -la {project_name}",
                        "description": "프로젝트 구조 확인"
                    }
                }]
            )
            plan.tasks.append(verify_task)
            
            # 7. 최종 검증
            final_task = Task(
                title="결과 검증",
                description="모든 작업이 올바르게 완료되었는지 확인",
                type=TaskType.VERIFICATION,
                dependencies=[verify_task.id]
            )
            plan.tasks.append(final_task)
    
    def _extract_project_name(self, query: str) -> str:
        """질문에서 프로젝트명 추출"""
        # 간단한 프로젝트명 추출
        patterns = [
            r'([a-zA-Z0-9_-]+)(?:\s*(?:라는|이라는|이름의|으로))?\s*(?:프로젝트|project)',
            r'(?:프로젝트|project)\s*(?:이름은|명은)?\s*([a-zA-Z0-9_-]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                return match.group(1)
        
        return "my-project"  # 기본값
    
    def _add_file_operation_tasks(self, plan: WorkflowPlan, query: str):
        """파일 조작 태스크들 추가"""
        # TODO: 파일 작업 태스크 구현
        pass
    
    def _add_installation_tasks(self, plan: WorkflowPlan, query: str):
        """설치 태스크들 추가"""
        # TODO: 설치 작업 태스크 구현
        pass
    
    def _add_execution_tasks(self, plan: WorkflowPlan, query: str):
        """실행 태스크들 추가"""
        # TODO: 실행 작업 태스크 구현
        pass