#!/usr/bin/env bash
set -e

# 参数解析
PYTHON_CMD=""
NO_UPGRADE_PIP=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      PYTHON_CMD="$2"
      shift 2
      ;;
    --no-upgrade-pip)
      NO_UPGRADE_PIP=true
      shift
      ;;
    *)
      echo "未知参数: $1"
      exit 1
      ;;
  esac
done

# 获取项目根目录 (scripts 目录的上一级)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"

# 自动寻找系统中的 Python 命令
if [ -z "$PYTHON_CMD" ]; then
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo "错误: 未找到 Python 命令，请先安装 Python 3.11 或更新版本。"
        exit 1
    fi
fi

# 检查 Python 版本是否 >= 3.11
"$PYTHON_CMD" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" || {
    echo "错误: 需要 Python 3.11 或更高版本。"
    exit 1
}

# 创建虚拟环境（如果不存在）
if [ ! -d "$VENV_PATH" ]; then
    echo "正在创建虚拟环境: $VENV_PATH ..."
    "$PYTHON_CMD" -m venv "$VENV_PATH"
fi

# Linux/macOS 下虚拟环境中的 Python 路径（区别于 Windows 的 Scripts/python.exe）
VENV_PYTHON="$VENV_PATH/bin/python"

# 升级 pip
if [ "$NO_UPGRADE_PIP" = false ]; then
    echo "正在升级 pip ..."
    "$VENV_PYTHON" -m pip install --upgrade pip
fi

# 安装锁定依赖
echo "正在安装构建依赖 (requirements-build.lock) ..."
"$VENV_PYTHON" -m pip install --require-hashes -r "$PROJECT_ROOT/requirements-build.lock"

echo "正在安装运行依赖 (requirements.lock) ..."
"$VENV_PYTHON" -m pip install --require-hashes -r "$PROJECT_ROOT/requirements.lock"

# 以可编辑模式安装当前 Agent 项目
echo "正在安装项目 arena-hero-agent ..."
"$VENV_PYTHON" -m pip install --no-deps --no-build-isolation --editable "$PROJECT_ROOT"

# 检查依赖一致性
echo "正在检查依赖一致性 ..."
"$VENV_PYTHON" -m pip check

echo "=========================================="
echo "环境准备完成！"
echo "你可以通过运行以下命令激活虚拟环境并开始使用："
echo "  source .venv/bin/activate"
echo "=========================================="