# 安装与故障恢复

macOS / Linux 安装入口：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.sh)
```

## 自动准备环境

安装器优先复用已有 Python 3.11+。macOS 缺少可用 Python 时，按下面的顺序完成准备：

1. 查找 PATH、Apple Silicon 和 Intel 的标准 Homebrew 安装目录。
2. 没有 Homebrew 时，下载并运行其官方安装脚本。交互终端保留管理员密码和确认提示，支持 `bash <(curl …)` 和 `curl … | bash`。
3. 通过 Homebrew 安装 Python 3.13，激活当前安装进程的 PATH，再重新检测 Python。
4. 检查 Git；缺少 Git 时也先准备 Homebrew，再安装 Git。

Homebrew 可能需要安装 Apple Command Line Tools，请按官方安装器的提示完成。其安装要求见 [Homebrew 官方文档](https://docs.brew.sh/Installation)。在无人值守环境中，设置 `NONINTERACTIVE=1`；首次安装 Homebrew 需要预先具备无需交互的管理员权限。安装器不会绕过系统授权。

已有 Python 的机器无需额外安装 Homebrew。可用 `HUSHCLAW_PYTHON` 指定解释器；安装器也会检查 SSL 等关键标准库能否正常加载。

## 进度与日志

终端按编号展示环境检查、代码更新、数据准备、后台启动等阶段。依赖安装等耗时步骤显示用时；详细命令输出保存在安装目录的 `logs/install-*.XXXXXX` 文件，失败时显示末尾诊断和具体路径。服务运行日志为 `~/.hushclaw/hushclaw.log`。使用 `HUSHCLAW_HOME` 时，两类日志随安装目录一起迁移。

只有本地 `/personal` 返回 HushClaw 页面后，后台安装才显示完成和访问地址。终端不支持颜色、输出重定向或设置 `NO_COLOR=1` 时，不输出 ANSI 颜色控制码。

## 升级中断

安装器先获取更新，再暂停服务应用代码。安装脚本重新执行时保留原始参数，并兼容 macOS 自带 Bash 3.2 的无参数调用。

如果安装器已停止原先运行的服务，后续失败时会尝试重新启动，并检查页面是否恢复。这个过程是**尝试恢复服务，不是代码、依赖或数据库回滚**；失败的更新仍会返回非零退出码。若恢复未成功，请根据安装日志解决问题，再启动已有安装：

```bash
bash ~/.hushclaw/repo/install.sh --start-only
```

仍使用旧安装器且遇到 `ORIGINAL_ARGS[@]: unbound variable` 时，应重新获取修复版安装器。不要反复卸载或删除数据。

## 端口冲突

安装器会核对进程身份。PID 文件过期或另一个程序占用 `8765` 时，不会把它当作 HushClaw，也不会停止那个程序。先关闭占用端口的应用，或选择其他端口：

```bash
HUSHCLAW_PORT=8767 bash install.sh
```

选择端口时应同时留出下一个端口，供 HTTP POST 代理使用。`HUSHCLAW_HOST` 保留现有绑定地址配置；仅供本机使用时可设置为 `127.0.0.1`。
