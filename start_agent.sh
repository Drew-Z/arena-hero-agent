#!/usr/bin/env bash
set -u

# 项目根目录获取
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 默认参数定义
PYTHON_PATH=""
ENV_FILE=".env"
LOG_FILE="arena_farmer.log"
WORKER_TARGET=18
BEACON_POLICY="pursue"
HISTORY_DB="arena_history.sqlite3"
BASE_URL=""
DASHBOARD_PORT=8765
NO_DASHBOARD=false
NO_COMPATIBILITY_MARKER=false

# 路径解析函数
resolve_path() {
    local val="$1"
    if [[ "$val" = /* ]]; then
        echo "$val"
    else
        echo "$PROJECT_ROOT/$val"
    fi
}

# 命令行参数解析
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-path) PYTHON_PATH="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --log-file) LOG_FILE="$2"; shift 2 ;;
    --worker-target) WORKER_TARGET="$2"; shift 2 ;;
    --beacon-policy) BEACON_POLICY="$2"; shift 2 ;;
    --history-db) HISTORY_DB="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --dashboard-port) DASHBOARD_PORT="$2"; shift 2 ;;
    --no-dashboard) NO_DASHBOARD=true; shift ;;
    --no-compatibility-marker) NO_COMPATIBILITY_MARKER=true; shift ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

# Python 解释器路径解析
if [ -z "$PYTHON_PATH" ]; then
    PYTHON_PATH="$PROJECT_ROOT/.venv/bin/python"
else
    PYTHON_PATH="$(resolve_path "$PYTHON_PATH")"
fi

AGENT_PATH="$PROJECT_ROOT/arena_farmer.py"
DASHBOARD_PATH="$PROJECT_ROOT/arena_dashboard.py"
ENV_PATH="$(resolve_path "$ENV_FILE")"
LOG_PATH="$(resolve_path "$LOG_FILE")"
HISTORY_PATH="$(resolve_path "$HISTORY_DB")"
DASHBOARD_URL="http://127.0.0.1:${DASHBOARD_PORT}/"
DASHBOARD_LOG_PATH="$PROJECT_ROOT/arena_dashboard.log"
DASHBOARD_ERROR_LOG_PATH="$PROJECT_ROOT/arena_dashboard.error.log"

MAX_LOG_BYTES=$((5 * 1024 * 1024)) # 5MB
LOG_BACKUP_COUNT=3

# 检查虚拟环境
if [ ! -f "$PYTHON_PATH" ]; then
    echo "错误: Python 虚拟环境不存在。请先运行 ./scripts/bootstrap.sh" >&2
    echo "预期路径: $PYTHON_PATH" >&2
    exit 1
fi

# 日志轮转函数
rotate_log() {
    local target_log="$1"
    if [ ! -f "$target_log" ]; then return; fi
    
    local size
    size=$(stat -c%s "$target_log" 2>/dev/null || stat -f%z "$target_log" 2>/dev/null || echo 0)
    if [ "$size" -lt "$MAX_LOG_BYTES" ]; then return; fi

    local oldest="$target_log.$LOG_BACKUP_COUNT"
    if [ -f "$oldest" ]; then rm -f "$oldest"; fi

    for ((i=LOG_BACKUP_COUNT-1; i>=1; i--)); do
        local src="$target_log.$i"
        local dst="$target_log.$((i+1))"
        if [ -f "$src" ]; then mv -f "$src" "$dst"; fi
    done
    mv -f "$target_log" "$target_log.1"
}

# API Key 检查与交互式补全
key_in_env=false
if [ -n "${ARENA_HERO_API_KEY:-}" ]; then key_in_env=true; fi

key_in_file=false
if [ -f "$ENV_PATH" ]; then
    if grep -E '^\s*ARENA_HERO_API_KEY\s*=\s*\S+' "$ENV_PATH" | grep -qvE '^\s*ARENA_HERO_API_KEY\s*=\s*(replace-with|your-|<)'; then
        key_in_file=true
    fi
fi

if [ "$key_in_env" = false ] && [ "$key_in_file" = false ]; then
    echo "未检测到 Arena Hero API key。密钥将被追加写入 $ENV_PATH"
    read -r -s -p "请输入当前的 Arena Hero API key: " plain_key
    echo ""
    plain_key="$(echo "$plain_key" | xargs)"
    if [ -z "$plain_key" ]; then
        echo "错误: API key 不能为空。" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$ENV_PATH")"
    if [ -f "$ENV_PATH" ] && [ -s "$ENV_PATH" ]; then
        # 确保以换行符结尾
        sed -i -e '$a\' "$ENV_PATH" 2>/dev/null || true
    fi
    echo "ARENA_HERO_API_KEY=$plain_key" >> "$ENV_PATH"
fi

# 构建 Agent 启动参数数组
AGENT_ARGS=(
    "$AGENT_PATH"
    "--env-file" "$ENV_PATH"
    "--worker-target" "$WORKER_TARGET"
    "--beacon-policy" "$BEACON_POLICY"
    "--history-db" "$HISTORY_PATH"
)
if [ -n "$BASE_URL" ]; then
    AGENT_ARGS+=("--base-url" "$BASE_URL")
fi
if [ "$NO_COMPATIBILITY_MARKER" = true ]; then
    AGENT_ARGS+=("--no-compatibility-marker")
fi

# Dashboard 状态检测
test_dashboard_ready() {
    curl -s -m 1 "${DASHBOARD_URL}api/overview" > /dev/null 2>&1
    return $?
}

DASHBOARD_PID=""

# 启动 Dashboard 后台进程
start_dashboard() {
    if test_dashboard_ready; then
        echo "Dashboard 已经在运行中: $DASHBOARD_URL"
        return 0
    fi

    "$PYTHON_PATH" "$DASHBOARD_PATH" \
        --history-db "$HISTORY_PATH" \
        --host "127.0.0.1" \
        --port "$DASHBOARD_PORT" \
        > "$DASHBOARD_LOG_PATH" 2> "$DASHBOARD_ERROR_LOG_PATH" &
    DASHBOARD_PID=$!

    for ((attempt=0; attempt<20; attempt++)); do
        if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
            echo "Dashboard 启动失败，请查看日志 $DASHBOARD_ERROR_LOG_PATH" >&2
            exit 1
        fi
        if test_dashboard_ready; then
            echo "Dashboard 启动成功: $DASHBOARD_URL"
            return 0
        fi
        sleep 0.25
    done
    kill -9 "$DASHBOARD_PID" 2>/dev/null || true
    echo "Dashboard 超时未就绪，请查看日志 $DASHBOARD_ERROR_LOG_PATH" >&2
    exit 1
}

# 单实例进程锁控制 (使用 Linux flock)
STATE_DIR="$PROJECT_ROOT/state"
mkdir -p "$STATE_DIR"
LOCK_FILE="$STATE_DIR/linux-agent.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "Arena Hero Agent 已经在运行中，请勿重复启动。" >&2
    exit 2
fi

# 退出清理钩子
cleanup() {
    if [ -n "$DASHBOARD_PID" ] && kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        kill "$DASHBOARD_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 尝试启动 Dashboard
if [ "$NO_DASHBOARD" = false ]; then
    start_dashboard
    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
	if command -v xdg-open &>/dev/null; then
           xdg-open "$DASHBOARD_URL" &>/dev/null &
        fi
    fi
fi

# 主挂机循环与指数退避重连
TRANSIENT_EXIT_CODE=75
RETRY_DELAY=2
MAX_RETRY_DELAY=30

while true; do
    rotate_log "$LOG_PATH"
    RUN_STARTED_AT=$(date +%s)

    # 运行 Agent 并将输出实时打印并写入日志
    "$PYTHON_PATH" "${AGENT_ARGS[@]}" 2>&1 | tee -a "$LOG_PATH"
    AGENT_EXIT_CODE=${PIPESTATUS[0]}

    if [ "$AGENT_EXIT_CODE" -ne "$TRANSIENT_EXIT_CODE" ]; then
        break
    fi

    NOW=$(date +%s)
    if [ $((NOW - RUN_STARTED_AT)) -ge 300 ]; then
        RETRY_DELAY=2
    fi

    echo "警告: Agent 发生临时故障。将于 ${RETRY_DELAY} 秒后重新启动..." >&2
    sleep "$RETRY_DELAY"
    RETRY_DELAY=$(( RETRY_DELAY * 2 ))
    if [ "$RETRY_DELAY" -gt "$MAX_RETRY_DELAY" ]; then
        RETRY_DELAY=$MAX_RETRY_DELAY
    fi
done

echo "Agent 已停止，退出代码: $AGENT_EXIT_CODE"
exit "$AGENT_EXIT_CODE"
