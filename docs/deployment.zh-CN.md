# 服务器部署与运维

本文提供 Linux systemd 无人值守部署的中文操作入口。完整的发行版支持矩阵、
安全隔离细节和卸载范围以[英文部署文档](deployment.md)为准。

## 环境要求

- GNU/Linux 与 systemd 235+；systemd 247+ 才能应用完整隔离策略；
- Python 3.11+，并且 `python -m venv` 与 pip 可用；
- Git、tar、flock、GNU coreutils、util-linux 和系统账号管理工具；
- 能访问 Arena Hero、GitHub 和所选 Python 包索引。

Ubuntu 22.04 默认 Python 3.10，需要另行安装系统级 Python 3.11+ 及匹配的
`venv` 包。AlmaLinux、Rocky Linux 和 RHEL 9 也建议显式选择 Python：

```bash
sudo sh scripts/install-systemd.sh --python "$(command -v python3.11)"
```

## 首次安装

在已经检出的项目目录执行：

```bash
sudo sh scripts/install-systemd.sh
```

安装器会隐藏输入 Arena Hero API key，构建不可变 release，通过依赖、CLI、
版本兼容和健康检查后才原子切换 `current`。失败时保留或恢复旧 release。

默认启用 `arena-hero-agent.service` 和
`arena-hero-version-monitor.timer`。Supervisor、模型复盘和 root optimizer
都是可选组件，不影响主策略运行。

## 检查状态

```bash
sudo systemctl status arena-hero-agent.service --no-pager
sudo /opt/arena-hero-agent/current/.venv/bin/arena-hero-health
cat /opt/arena-hero-agent/current/source-commit
```

健康检查会核对服务、最新有效心跳和版本兼容状态。不要把包含玩家标识、坐标、
完整运行日志或凭据的输出直接发布到 Issue。

## 快速更新策略

在服务器的 Git checkout 中，以 checkout 所有者身份运行，不要加 `sudo`：

```bash
sh scripts/update-systemd.sh
```

更新器只接受干净、具有 upstream 且可以快进的分支。它先归档远端目标 commit，
再在 root 专属临时目录构建新版。旧策略在构建期间继续运行；激活时 systemd 先
停止旧进程，再启动新版，因此不会并行运行两个主 Agent。

更新后核对：

```bash
sudo /opt/arena-hero-agent/current/.venv/bin/arena-hero-health
cat /opt/arena-hero-agent/current/source-commit
```

## Python 镜像尚未同步

如果系统配置的 Python 镜像还没有同步锁文件中的新版本，pip 会报告
`No matching distribution found`。不要降低依赖版本，也不要删除锁文件哈希。
对本次更新显式使用可信 HTTPS 索引：

```bash
ARENA_PIP_INDEX_URL=https://pypi.org/simple sh scripts/update-systemd.sh
```

该变量会穿过更新器的 sudo 边界，但仅影响这次事务。安装阶段禁用 pip 配置
文件、替换主索引、清空额外索引，并继续验证所有锁定哈希。包含账号信息、
空白字符、HTTP 或其他非 HTTPS 协议的地址会被拒绝。命令不会修改系统或用户
的 pip 配置。

## 回滚

成功升级后，上一个不可变 release 保留在 `previous`：

```bash
sudo arena-hero-rollback
sudo /opt/arena-hero-agent/current/.venv/bin/arena-hero-health
```

回滚不重新下载依赖。命令会验证 release、切换链接、检查兼容性并重启服务；
失败时尝试恢复原来的链接和服务状态。

## 停止运行

只停止游戏 Agent：

```bash
sudo systemctl stop arena-hero-agent.service
```

停止并禁用所有无人值守任务：

```bash
sudo systemctl disable --now arena-hero-agent.service
sudo systemctl disable --now arena-hero-version-monitor.timer
sudo systemctl disable --now arena-hero-supervisor.timer
sudo systemctl disable --now arena-hero-optimizer.timer
```

Supervisor 和 optimizer 是 oneshot 服务；如果它们正在执行，再分别停止对应的
`.service`。完整卸载命令及删除范围见[英文部署文档](deployment.md#uninstall)。

## 凭据要求

- Arena Hero key 与模型 key 分开保存；
- 不要写入 Compose、systemd unit、截图、Issue 或日志；
- 安装前把私密配置权限设为 `0600`；
- key 一旦出现在聊天或公开内容中，应立即轮换，删除文本并不能使旧 key 失效。
