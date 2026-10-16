# YanCodexChat

`YanCodexChat` 是一个真正的单文件 Windows 桥接程序。用户只需要保留并编辑 `CodexChat.bat`，Telegram 消息会通过电脑上正在运行的 Codex 桌面 app-tools 接口进入选中的 Codex 对话，回复再返回 Telegram。

## 使用前提

- Windows 10/11（64 位）；
- Codex 桌面端已登录并正在运行；
- Telegram Bot Token 和 Telegram User ID；
- 能连接 Telegram，首次准备运行环境时能访问 python.org 和 nodejs.org。

## 使用方法

1. 只复制 `CodexChat.bat` 到任意目录；
2. 用文本编辑器打开它；
3. 只修改文件顶部的 `TELEGRAM_BOT_TOKEN`；
4. 只修改 `ALLOWED_USER_ID`，多个用户用英文逗号分隔；
5. 保存后双击运行。

BAT 内嵌了启动脚本、Python 桥接代码和 Codex 本地接口客户端，不依赖旁边的 `bridge.py` 或 `app_tools_client.cjs`。首次启动时，它会在 BAT 所在目录自动生成运行目录、日志和临时运行文件；用户不需要编辑或运行任何其他文件。

启动器优先使用已安装的 Python 3.10+ 和 Node.js 16+；缺少时，从 Python / Node 官方站点下载便携运行环境到 `runtime`，不修改系统 PATH，无需管理员安装命令。当前桥接只使用标准库，无需 pip、npm 包或 Codex CLI。Codex 桌面端仍需自行安装、登录并保持运行。

BAT 路径可以包含中文、空格，文件可复制到其他目录或盘符。仅复制 BAT 会作为新安装启动；需要保留绑定和桥接本地记录时一并复制 `data`，图片文件保存在 `telegram_media`。完整对话仍保存在原电脑的 Codex 中，不会因为复制 BAT 自动转移到另一台电脑。

填写 Token 后不要公开分享这个 BAT。仓库中的 BAT 只有占位符。

## YanCodexChat skill

普通用户只需使用 BAT。需要让 Codex 复用部署和修复方法时，可在 [Releases](https://github.com/Yan16384/YanCodexChat/releases) 下载 `yancodexchat.skill`；其中附带同版、未填写凭证的 `assets/CodexChat.bat`。

新版 skill 明确要求：启动探测先自动取得已有本机会话 ID，启动上下文不改变 Telegram 目标绑定；`Invalid app tool request` 等参数错误不能误报为“桌面未运行”；修改代码后必须同时刷新 BAT 和 skill 内的 BAT。开发者可运行 `python scripts/check_template.py` 检查内嵌代码、凭证占位符及模拟状态库中的启动逻辑。此检查不会发送 Telegram 消息，也不代表完整收发验证已通过。

## Telegram 操作

发送 `/start` 打开菜单：

- `1 设备对话（最近10条）`：查看最近设备对话；
- `2 置顶对话（全部）`：查看置顶对话；
- 点击会话进入 Codex 原对话；
- `/stop`：退出当前会话；
- `/history`：查看最近五组问答；
- `/status`：查看状态；
- `/questions` 或菜单“待回答问题”：显示当前会话最新一组未回答的选择题；
- `/cancel`：取消“文字回答”输入，保留原问题；
- `/bind SESSION_ID` 或 `/use SESSION_ID`：手动绑定会话。

支持普通文字、图片和图片配文。图片先下载到本机，发送给 Codex 的是图片路径及配文，需要 Codex 读取本地图片；这不是原生图片附件接口。历史记录按“我 / Codex”成对显示并保留空行。电脑端主动发送的消息不会自动推送到 Telegram。

### 选择题与文字回答（v1.2.0）

Codex 发出异步问题时，机器人会显示原题、各个选项按钮，以及“文字回答”和“跳过此题”。点击选项即可提交；点击“文字回答”后，下一条消息作为该题回答。进入已有会话后，也可以用 `/questions` 找回未回答的问题。

回答带着原问题 ID，经桌面的原生输入通道回到同一个 Codex 会话。机器人在 Codex 工作期间仍能接收点击。已提交、电脑端已回答、被新问题替换或属于其他会话的按钮会被拦截；发送结果不确定时不自动重发。退出或切换会话会取消尚未发送的排队消息，已经送入桌面的任务继续运行。

本版适配 `request_user_input_async` 问题卡，不包括工具执行审批、文件修改审批或阻塞式工具输入。只有手机发起的任务处理期间和主动查询时发现问题；不会持续监听并推送电脑端所有活动。

## 启动结果与日志

BAT 会检查 Python、Node.js、Telegram Bot `getMe`、Codex app-tools 接口、polling 初始化和单实例锁。只有对应后台进程完成健康检查后才显示已启动。运行日志在 `logs\bridge.log` 和 `logs\bridge-error.log`，启动诊断在 `logs\launcher.log`，状态在 `runtime\health.json`，绑定记录在 `data\bridge.sqlite3`。

如果提示服务已经运行，不会启动第二个 polling 实例；同一 Windows 登录会话中，复制到其他目录也不能重复启动同一个 Bot。其他电脑上的实例需要自行停止，Telegram 不支持同一 Bot 多处 polling。

关闭启动窗口不会停止后台服务。停止服务时查看 `runtime\bridge.pid` 中的 PID，在任务管理器“详细信息”中仅结束该 PID 对应的 Python 进程；重启时再双击同一个 BAT。不要结束其他 Python 任务。

不要删除运行中的 `runtime`；`data` 保存绑定和本地记录，`telegram_media` 保存图片，清理会丢失相应数据。交付单文件是指只需下载和复制一个 BAT，运行后仍会自动创建必要的数据文件。

## 接口兼容与验证范围

本项目沿用已有的 Codex 桌面 app-tools 管道协议，它依赖安装版本实际开放该接口，不是公开稳定的 Codex App Server 协议。若提示“找不到 Codex 桌面本地接口”，本版本不能在该桌面环境下转发，重新填写 Token 也不能解决此接口兼容问题。

异步问题回答另外使用桌面内部 native IPC follower 接口。该接口同样随桌面版本变化；普通转发可用不代表原生回答通道也可用，不兼容时会显示错误并保留会话绑定。

此次单文件改版已验证：从只含 BAT 的目录解包、中文和空格路径、从其他工作目录运行、关闭窗口后后台存活、重复启动拦截，以及仅有系统 PATH 时从官方站点自动下载和使用便携 Python / Node。打包检查使用模拟服务。后续修复了启动探测缺少会话 ID 的问题：现已在实际电脑通过 Telegram getMe、polling 初始化与 Codex list_threads 检查，并读到四个置顶对话；本次没有执行真实 Telegram 消息与 Codex 回复的完整收发验证。

v1.2.0 已通过离线交互检查：六选项、短回调、用户/会话校验、防重复提交、文字/取消/跳过、桌面已回答失效处理、重启后的不确定状态，以及原生 start/steer 请求格式和写操作不重试。实际桌面已通过原生回答通道初始化、会话 owner 发现和模型设置读取，原会话的一道六选项问题已通过 Telegram API 成功发送为按钮卡。真人点击后，原问题消失并收到对应回复的完整链路仍需在目标电脑实际确认。开发检查命令为 `python scripts/check_template.py`、`python scripts/check_interactions.py`、`node scripts/check_native.cjs`；普通用户无需执行。

## 安全提示

不要把填写过 Token 的 BAT 上传到公共仓库、发送给他人或提交到 Git。Telegram Bot Token 等同于密码。

## 许可证

MIT License。
