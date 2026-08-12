# Arena Hero 暴兵扩张 Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml)
[![发布镜像](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml)
[![许可证](https://img.shields.io/github/license/WuDiWangWaSai/arena-hero-agent)](LICENSE)

这是由 [WuDiWangWaSai](https://github.com/WuDiWangWaSai) 维护的 [Arena Hero](https://doc.arenahero.io/zh-Hans/) 确定性暴兵扩张策略。项目使用官方 Python SDK，并提供带历史视野回放和公开排行榜的战术展示页。

这是社区项目，不是 Arena Hero 官方产品。

社区认可：本项目认可并支持 [LINUX DO 社区](https://linux.do/)。

## 当前策略

默认兵力分三阶段持续生产：

| 阶段 | Worker（工兵） | Vanguard（先锋） | Ranger（游侠） | 总人口 |
| --- | ---: | ---: | ---: | ---: |
| 建立基地 | 6 | 2 | 2 | 10 |
| 全面动员 | 12 | 6 | 8 | 26 |
| 暴兵压制 | 18 | 14 | 16 | 48 |

48 人口时，Core 容量按 `max(10, population * 5)` 提高到 240。

- 进攻期间仍持续生产，并保留少量 Core 修复资源；紧急补兵可以动用这部分储备。
- 可见敌方 Core 是最高优先级进攻目标。看见护卫或远程拦截不会自动取消 Core 远征。
- Core 身边只固定保留 1 个 Vanguard 和 1 个 Ranger，其余战斗单位主动攻击、追击可见敌人，或向外扩大巡逻范围。
- 人口达到 40、资源达到 30，且 Core 满血满盾、没有直接威胁时，会派出一个非近卫 Vanguard 争夺 Champion Beacon。
- Core 不为日常扩张或信标主动搬家。生产格由 Worker 主动让开，只有确认生存威胁时 Core 才迁移。
- 游戏协议没有真正的“领土占领”命令。本项目中的扩张表示累计视野、向外巡逻、清除敌人和控制周边地图。

策略面向玩法规则 v0.14 和 `arena-hero` SDK 0.2.9。详细说明见[策略文档](docs/strategy.md)、[威胁响应](docs/threat-response.md)和[配置文档](docs/configuration.md)。

## 战术展示页

每个成功提交的 Turn 都会写入有上限的 SQLite 历史库。展示页支持：

- 当前地图和历史 Tick 回放；
- 已探索格、障碍、资源点和历史敌方 Core；
- 己方单位移动轨迹和当前计划移动线；
- 时间轴、播放、前后 Tick、拖拽和缩放；
- 事件流，以及伤害、摧毁 Core 参与次数、信标占领时长排行榜。
- 调兵面板：从当前己方单位中勾选具体 UUID，指定坐标并可取消进行中的订单；
- 我的战果：按历史事件统计单位/Core 摧毁参与次数，并显示敌方 Core 用户名；
- 谁打了我与复仇名单：记录受击、阵亡和协议明确提供的攻击者用户名，兵力达标后优先攻击已确认仇敌的 Core。

Agent 开始生成 `arena_history.sqlite3` 后，启动展示页：

```powershell
.\.venv\Scripts\python.exe -m arena_dashboard --history-db .\arena_history.sqlite3
```

浏览器打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。排行榜只访问 Arena Hero 公开接口，不会发送 Agent API Key。

## 环境要求

- Python 3.11 或更高版本
- Arena Hero API Key
- 本地运行仅支持 Windows PowerShell/CMD

运行依赖已通过哈希锁定。密钥和私有运行日志均不得提交到 Git。

## Windows 运行

在 PowerShell 中执行：

```powershell
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

如果 `.env` 和 `ARENA_HERO_API_KEY` 都没有密钥，脚本第一次运行时会安全提示输入。默认写入 `arena_farmer.log` 和 `arena_history.sqlite3`，在同一个 CMD 窗口中管理 Agent 和展示页、自动打开浏览器、轮转日志并重试临时故障。如只运行 Agent，可加 `-NoDashboard`。

在 CMD 中使用包装脚本：

```bat
start_agent.cmd
```

PowerShell 可选参数使用单横线：

```powershell
.\start_agent.ps1 -WorkerTarget 18 -BeaconPolicy pursue -HistoryDb .\arena_history.sqlite3
```

前台运行时按 `Ctrl+C` 停止，脚本会同时关闭由它启动的展示页进程。修改代码后必须重启 Agent 才会生效。

## 生产部署说明

仓库仍保留由独立生产服务器使用的 systemd 事务更新脚本。它们不属于本机 Windows 运行流程，因此不会在本地启动或验证。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover
.\.venv\Scripts\python.exe -m compileall -q arena_farmer.py arena_history.py arena_dashboard.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\check_secrets.py
```

## 许可证

项目使用 [Apache-2.0](LICENSE) 许可证。安全问题请按 [SECURITY.md](SECURITY.md) 提交，贡献代码请遵循 [CONTRIBUTING.md](CONTRIBUTING.md)。
