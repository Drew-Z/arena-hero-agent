# Arena Hero 无人值守 Agent

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个面向 [Arena Hero](https://doc.arenahero.io/zh-Hans/) 的确定性、资源优先长期运行 Agent。项目使用官方 `arena-hero` Python SDK，可在本地、Docker 或 Linux systemd 环境运行。

这是社区项目，并非 Arena Hero 官方产品。

## 主要能力

- 人口规划固定为 `12 Worker + 3 Vanguard + 4 Ranger = 19`，避免达到 20 人后触发资源惩罚。
- Core 主动远离信标，以采集和生存为主，同时让防卫单位分层保护，避免堵塞 Core 路线。
- 对地图陈旧区域进行侦察，记忆资源点，安排返程交付，并在损失后回收掉落资源。
- 遇到活跃敌方舰队时优先拉扯避战；对确认静止且孤立的威胁或 Core 执行有限清除。
- 定时检测游戏规则和 SDK 版本，发现不兼容时进入保守模式。
- 大模型不参与每个 Tick 的决策。可选模型只在异常触发后分析监督报告。

## 环境要求

- Python 3.11 或更高版本
- Arena Hero API key
- Docker 部署需要 Docker Compose v2
- 服务器无人值守部署需要 systemd Linux

当前验证的协议是 API `v0.1`、玩法规则 `v0.11`、官方 Python SDK `0.2.6`。

## 最快开始

### Windows 本地

```powershell
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

首次启动会安全提示输入 Arena Hero key，并追加到已经被 Git 忽略的 `.env`。完成初始化后也可以双击 `start_agent.cmd`；如果启动失败，窗口会保留错误信息，不会闪退。

### Linux 或 macOS 本地

```bash
sh scripts/bootstrap.sh
cp .env.example .env
chmod 600 .env
# 编辑 .env，填写 ARENA_HERO_API_KEY。
sh scripts/run-agent.sh
```

### Docker Compose

```bash
mkdir -p secrets
cp secrets/arena_hero_api_key.example.txt secrets/arena_hero_api_key.txt
# 替换文件中的占位值，然后执行：
docker compose up -d --build
docker compose logs -f agent
```

Compose 会以 Docker secret 挂载 key。容器使用非特权用户和只读文件系统，默认不包含 supervisor 与 optimizer。

无需本地构建，直接使用发布镜像：

```bash
ARENA_HERO_AGENT_IMAGE=ghcr.io/drew-z/arena-hero-agent:0.1.0 docker compose up -d --no-build
```

### Linux 服务器 systemd

在服务器上的项目发布目录执行：

```bash
sudo sh scripts/install-systemd.sh
sudo journalctl -fu arena-hero-agent.service -o short-iso-precise
```

安装器会隐藏输入 API key，将程序安装到 `/opt/arena-hero-agent`，默认只启用主 Agent 和每六小时一次的版本兼容监控。

其余组件必须显式开启：

```bash
# 只读、确定性的异常监督，不使用模型。
sudo sh scripts/install-systemd.sh --with-supervisor

# 开启模型复盘；先根据示例准备私密配置文件。
sudo sh scripts/install-systemd.sh --with-ai /secure/path/supervisor.env

# root 权限运行时优化器；开启前必须阅读部署文档。
sudo sh scripts/install-systemd.sh --with-optimizer
```

## 模型监督是可选项

主 Agent 完全不需要模型。Supervisor 只有同时满足以下条件才会调用模型：

1. 明确设置 `ARENA_SUPERVISOR_AI_ENABLED=true`；
2. 确定性规则检测到异常；
3. 已配置接口地址、API key 和至少一个模型 ID。

模型结果只用于只读建议，不能提交游戏操作、修改策略或重启 Agent。独立 optimizer 可以修改少量运行参数并重启 systemd 服务，因此需要 root，默认关闭。

完整说明见 [配置文档](docs/configuration.md)、[部署文档](docs/deployment.md) 和 [策略文档](docs/strategy.md)。

首次公开提交前请按 [发布检查清单](docs/release-checklist.md) 检查凭据、日志和 Git 暂存区。

## 开发与验证

```bash
python -m pip install -e .
python -m unittest discover -v
python -m compileall -q arena_farmer.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
python scripts/check_secrets.py
```

测试全部使用构造数据，不需要真实 API key，也不会连接线上游戏。

## 安全

不要提交 `.env`、模型渠道配置、Docker secret、运行日志或 systemd 凭据。任何 key 一旦出现在聊天、日志、Issue 或 Git 历史中，都应立即轮换；仅删除文本无法使旧 key 失效。

安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。项目按 [Apache License 2.0](LICENSE) 开源。
