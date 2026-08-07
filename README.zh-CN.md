# Arena Hero 无人值守 Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml)
[![发布镜像](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml)
[![许可证](https://img.shields.io/github/license/WuDiWangWaSai/arena-hero-agent)](LICENSE)

这是一个由 [WuDiWangWaSai](https://github.com/WuDiWangWaSai) 维护的 [Arena Hero](https://doc.arenahero.io/zh-Hans/) 确定性、资源优先长期运行策略。项目使用官方 Python SDK，支持 Windows、Linux、Docker 和加固的 systemd 部署。

这是社区项目，不是 Arena Hero 官方产品。

## 当前策略

默认阵容目标：

| 单位 | 数量 | 用途 |
| --- | ---: | --- |
| Worker（工兵） | 23 | 采集、回仓、侦察和回收掉落资源 |
| Vanguard（先锋） | 3 | Core 外层防御和路线保护 |
| Ranger（游侠） | 4 | Core 内层防御和远程反击 |
| 总人口 | 30 | 将 Core 资源容量提高到 150 |

策略已适配玩法规则 v0.14 和 `arena-hero` SDK 0.2.9：

- 先建立早期经济与最低防线；达到 12 个 Worker 后补齐 `3 Vanguard + 4 Ranger`，再继续扩充 Worker。
- 按人口 20、24、29、30 分阶段准备资源，避免动态价格上涨后因临时资源不足卡住生产。
- 总人口严格限制为 30。Core 容量公式为 `max(10, population * 5)`，完整阵容可储存 150 资源。
- Core 生产格有 Worker 时先让 Worker 离开；出口拥堵时执行确定性的走廊换位。Core 满仓时，满载 Worker 会留在外面，等容量恢复再回仓。
- 每个 Turn 都作为新的权威状态处理，动态更新资源点、避免多个 Worker 重复采集，并回收死亡 Worker 掉落的资源。
- 生命周期、威胁与任务分层决策；威胁状态包括 `NORMAL`、`ALERT`、`PRE_EVADE`、`ENGAGED` 和 `BREAKOUT`。
- 只有确认生存威胁时才迁移 Core；日常清理生产格只移动 Worker，不移动 Core。
- 支持 Ranger 无目标格射击、战后治疗、Core 重生恢复、兼容性暂停、心跳健康检查和结构化诊断。

详细规则见[策略说明](docs/strategy.md)、[威胁响应](docs/threat-response.md)和[配置说明](docs/configuration.md)。

## 运行要求

- Python 3.11 或更高版本
- Arena Hero API Key
- Windows 使用 PowerShell，Linux 使用 POSIX shell
- 只有选择对应部署方式时才需要 Docker 或 systemd

依赖采用哈希锁定，运行时固定使用 `arena-hero==0.2.9`。

## Windows

在 PowerShell 中执行：

```powershell
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

如果 `.env` 和 `ARENA_HERO_API_KEY` 都没有密钥，`start_agent.ps1` 第一次运行时会安全提示输入。日志写入 `arena_farmer.log`，脚本会自动轮转日志，并对退出码 75 的临时故障进行退避重试。

在 CMD 中运行时使用包装脚本：

```bat
start_agent.cmd
```

PowerShell 参数使用单横线和 PowerShell 参数名：

```powershell
.\start_agent.ps1 -WorkerTarget 23 -BeaconPolicy retreat
```

前台运行时按 `Ctrl+C` 停止。

## Linux

```bash
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
cp .env.example .env
# 只在本机编辑 .env，填写 ARENA_HERO_API_KEY，禁止提交。
./scripts/bootstrap.sh
./scripts/run-agent.sh
```

无需修改代码即可调整运行参数：

```bash
ARENA_WORKER_TARGET=23 ARENA_BEACON_POLICY=retreat ./scripts/run-agent.sh
```

## Docker Compose

将 API Key 作为唯一一行写入 `secrets/arena_hero_api_key.txt`，然后执行：

```bash
docker compose up -d --build
docker compose logs -f agent
```

使用已经发布的镜像：

```bash
ARENA_HERO_AGENT_IMAGE=ghcr.io/wudiwangwasai/arena-hero-agent:0.1.0 docker compose up -d --no-build
```

容器采用只读文件系统、移除 Linux capabilities、通过 Docker secret 读取密钥，并用成功接收的 Turn 作为健康状态依据。

## systemd

在受支持的 Linux 主机安装主 Agent 和版本监控：

```bash
sudo sh scripts/install-systemd.sh
sudo systemctl status arena-hero-agent.service --no-pager
sudo journalctl -fu arena-hero-agent.service -o short-iso-precise
```

可选组件需要显式启用：

```bash
sudo sh scripts/install-systemd.sh --with-supervisor
sudo sh scripts/install-systemd.sh --with-optimizer
```

Supervisor 负责确定性健康检查。Optimizer 只测试白名单内的 Worker 数量，并能重启或回滚服务；默认不启用。

生产更新必须使用事务式更新脚本：

```bash
sh scripts/update-systemd.sh
sudo systemctl is-active arena-hero-agent.service
cat /opt/arena-hero-agent/current/source-commit
```

支持的发行版、回滚、可选 AI 审查和卸载步骤见[部署文档](docs/deployment.md)。

## 测试与安全

```powershell
.\.venv\Scripts\python.exe -m unittest discover
.\.venv\Scripts\python.exe -m compileall -q arena_farmer.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\check_secrets.py
```

密钥只能放在环境变量、`.env`、Docker secret 或受保护的 systemd 配置中。禁止提交 API Key、玩家标识、私有日志或模型凭证。

## 许可证

项目使用 [Apache-2.0](LICENSE) 许可证。安全问题请遵循 [SECURITY.md](SECURITY.md)，贡献代码请遵循 [CONTRIBUTING.md](CONTRIBUTING.md)。
