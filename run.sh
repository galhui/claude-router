#!/bin/bash
set -e

echo "🟢 Claude Router 환경 세팅 시작..."

# -----------------------------
# 1. .claude 설정 생성
# -----------------------------
mkdir -p .claude
SETTINGS_FILE=".claude/settings.local.json"
cat <<EOF > "$SETTINGS_FILE"
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4000",
    "ANTHROPIC_API_KEY": "any-key",
    "ANTHROPIC_MODEL": "gpt-oss:20b"
  },
  "allowedTools": ["*"],
  "hasTrustDialogAccepted": true,
  "hasCompletedProjectOnboarding": true
}
EOF
echo "✅ .claude/settings.local.json 생성 완료"

# -----------------------------
# 2. Python 가상환경 설정
# -----------------------------
echo "📦 Python 가상환경 설정 중..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ 가상환경 생성 완료"
fi

# 가상환경 활성화
source venv/bin/activate
echo "✅ 가상환경 활성화 완료"

# -----------------------------
# 3. Python 패키지 설치
# -----------------------------
echo "📦 Python 패키지 설치 중..."
pip3 install -r requirements.txt
echo "✅ Python 패키지 설치 완료"

# -----------------------------
# 4. Ollama 서비스 확인
# -----------------------------
echo "🔍 Ollama 서비스 확인 중..."
if ! curl -s http://localhost:11434/api/version > /dev/null 2>&1; then
    echo "❌ Ollama가 실행되지 않았습니다. 먼저 Ollama를 시작해주세요:"
    echo "   ollama serve"
    exit 1
fi
echo "✅ Ollama 서비스 확인 완료"

# -----------------------------
# 5. GPT-OSS 모델 확인
# -----------------------------
echo "🔍 gpt-oss:20b 모델 확인 중..."
if ! ollama list | grep -q "gpt-oss:20b"; then
    echo "❌ gpt-oss:20b 모델이 설치되지 않았습니다. 먼저 모델을 설치해주세요:"
    echo "   ollama pull gpt-oss:20b"
    exit 1
fi
echo "✅ gpt-oss:20b 모델 확인 완료"

# -----------------------------
# 6. 포트 4000 확인 및 기존 서비스 종료
# -----------------------------
echo "🔍 포트 4000 확인 중..."
if lsof -ti:4000 > /dev/null 2>&1; then
    echo "⚠️  포트 4000이 이미 사용 중입니다. 기존 서비스를 종료합니다..."
    lsof -ti:4000 | xargs kill -9 2>/dev/null || true
    sleep 2
    echo "✅ 기존 서비스 종료 완료"
fi

# -----------------------------
# 7. Claude Router 백그라운드 실행
# -----------------------------
echo "🚀 Claude Router 백그라운드에서 실행 중..."
nohup uvicorn src.main:app --host 0.0.0.0 --port 4000 --reload > claude-router.log 2>&1 &
ROUTER_PID=$!
sleep 2

# 서비스 시작 확인
if curl -s http://localhost:4000/health > /dev/null 2>&1; then
    echo "✅ Claude Router 시작 완료 (PID: $ROUTER_PID)"
    echo "   - Claude Router: http://localhost:4000"
    echo "   - Ollama: http://localhost:11434"
    echo "   - 로그 파일: claude-router.log"
    echo ""
    echo "📋 상태 확인: curl http://localhost:4000/health"
    echo "📋 로그 확인: tail -f claude-router.log" 
    echo "🛑 종료: kill $ROUTER_PID"
    echo ""
    echo "🎉 모든 서비스가 준비되었습니다!"
else
    echo "❌ Claude Router 시작 실패"
    kill $ROUTER_PID 2>/dev/null
    exit 1
fi
