# Arena Hero 无人值守 Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/ci.yml)
[![发布镜像](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/release.yml/badge.svg)](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/release.yml)
[![许可证](https://img.shields.io/github/license/Drew-Z/arena-hero-agent)](LICENSE)

这是一个面向 [Arena Hero](https://doc.arenahero.io/zh-Hans/) 的确定性、资源优先长期运行 Agent。项目采用分层威胁控制器和官方 `arena-hero` Python SDK，可在本地、Docker 或 Linux systemd 环境运行。

这是社区项目，并非 Arena Hero 官方产品。

## 主要能力

- 默认编制为 `12 Worker + 4 Vanguard + 4 Ranger = 20`，完整使用基础价格区间；玩法 v0.14 从第 21 个单位开始提高生产价格。
- Core 主动远离信标，以采集和生存为主，同时让防卫单位分层保护，避免堵塞 Core 路线。
- 将生命周期、威胁等级和单位任务独立分类，覆盖活动警戒、提前撤离、交战、多轴突围和远征队归队。
- 对地图陈旧区域进行侦察，记忆资源点，安排返程交付，并在损失后回收掉落资源。
- 遇到活跃敌方舰队时优先拉扯避战；对确认静止且孤立的威胁或 Core 执行有限清除。
- 定时检测游戏规则和 SDK 版本，发现不兼容时进入保守模式。
- 大模型不参与每个 Tick 的决策。可选模型只在异常触发后分析监督报告。

## 环境要求

- Python 3.11 或更高版本
- Arena Hero API key
- Docker 部署需要 Docker Compose v2
- 服务器无人值守部署需要 GNU/Linux 和 systemd 235+；systemd 247+ 才能完整
  应用全部单元安全隔离配置

当前验证的协议是 API `v0.1`、玩法规则 `v0.14`、官方 Python SDK `0.2.9`。v0.14 已删除每 Tick 维护费和欠费伤害；第 21 个及后续单位采用动态生产价格。Agent 会使用官方 SDK 预估每次生产价格，并根据服务端结算事件核对实际消耗。

## 最快开始

先获取仓库并进入项目目录，再选择下面的部署方式：

```bash
git clone https://github.com/Drew-Z/arena-hero-agent.git
cd arena-hero-agent
```

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

POSIX 初始化脚本会自动探测带版本号的 Python 3.11+。如果系统的 `python3`
版本较旧，可使用
`PYTHON_BIN="$(command -v python3.11)" sh scripts/bootstrap.sh` 显式指定。

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

Ubuntu 22.04 默认是 Python 3.10。请安装系统级 Python 3.11+ 及匹配的
`venv` 包，并通过 `--python` 指定；Debian、Ubuntu、RHEL/Alma/Rocky、
Fedora、Arch 和 openSUSE 的差异详见
[Linux 支持矩阵](docs/deployment.md#linux-systemd-server)。

安装器会隐藏输入 API key，在 `/opt/arena-hero-agent/releases` 下构建不可变
版本并原子切换 `current` 链接，默认只启用主 Agent 和每六小时一次的版本兼容
监控。兼容检查、重启或健康检查失败时会自动恢复旧版本；升级成功后可执行
`sudo arena-hero-rollback` 立即切换回上一版本。

策略代码发布更新后，可在服务器仓库目录用一个命令切换正在运行的 systemd
游戏实例：

```bash
sh scripts/update-systemd.sh
```

请以仓库所有者身份直接运行，不要在命令前加 `sudo`。更新器只接受已配置
upstream 的干净分支，获取更新并确认可快进后，先构建、校验新版，再仅对事务
安装步骤提权。实际部署内容来自目标 commit 的精确归档，并在 root 专属临时目录
中构建，不会继续读取正在变化的仓库工作树。systemd 重启时会先停止旧策略进程，
再启动新版，不会并行运行两个主 Agent 实例。新版准备期间旧版会继续运行；已有
凭据、运行参数和已启用的可选组件都会保留。重启或健康检查失败时，安装器会
尝试恢复旧版本，并在自动恢复也失败时明确要求人工检查。

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

## 文档与社区

- [LINUX DO](https://linux.do/) - 本项目认可并支持的开源社区
- [文档索引](docs/README.md)
- [策略设计](docs/strategy.md)
- [参与贡献](CONTRIBUTING.md)
- [社区行为准则](CODE_OF_CONDUCT.md)
- [安全策略](SECURITY.md)

## 开发与验证

```bash
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m unittest discover -v
python -m compileall -q arena_farmer.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
python scripts/check_secrets.py
```

测试全部使用构造数据，不需要真实 API key，也不会连接线上游戏。CI 在 GitHub
托管的 Ubuntu 和 Windows 上覆盖 Python 3.11-3.13，并检查镜像构建和 systemd
单元；这不代表所有 Linux 发行版都完成了真实服务安装认证。

更新依赖时，使用锁文件头部记录的完整 `uv pip compile` 命令重新生成，
审查依赖差异并完成测试后再提交。

## 安全

不要提交 `.env`、模型渠道配置、Docker secret、运行日志或 systemd 凭据。任何 key 一旦出现在聊天、日志、Issue 或 Git 历史中，都应立即轮换；仅删除文本无法使旧 key 失效。

安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。项目按 [Apache License 2.0](LICENSE) 开源。
