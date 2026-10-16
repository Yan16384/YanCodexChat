# YanCodexChat

YanCodexChat 是一个 Windows 本地桥接服务：Telegram 负责手机端收发，消息通过 Codex 桌面端的本地 app-tools 接口进入电脑上已经打开的 Codex 对话，回复再返回 Telegram。桥接不会使用 Computer Use、远程点击窗口或 `codex exec resume`。

## 使用前提

- Windows 10/11
- Python 3、Node.js 和 npm 已加入 PATH
- Codex 桌面端已经登录并运行
- 一个 Telegram Bot Token
- 你的 Telegram User ID

## 部署

1. 复制整个项目目录到任意位置。路径可以包含中文、空格，也可以换盘符和 Windows 用户。
2. 用文本编辑器打开唯一入口 `CodexChat.bat`。
3. 只修改顶部的 `TELEGRAM_BOT_TOKEN`。
4. 只修改顶部的 `ALLOWED_USER_ID`。多个用户可用英文逗号分隔。
5. 保存并双击 `CodexChat.bat`。
6. 等待窗口显示 Telegram `getMe` 和 Codex `app-tools` 均为 ready。

BAT 内的 Token 是凭证，不要把填写后的文件公开分享或提交到 GitHub。仓库中的 BAT 只有占位符。

## 使用

启动后通过 Telegram 发送 `/start`：

- `1 设备对话（最近10条）`：查看最近设备对话；
- `2 置顶对话（全部）`：查看置顶对话；
- 点击会话后进入该 Codex 原对话；
- `/stop`：退出当前绑定；
- `/history`：查看当前会话最近五组问答；
- `/status`：查看绑定状态；
- `/bind SESSION_ID` 或 `/use SESSION_ID`：手动绑定会话。

普通文字会写入电脑端选中的 Codex 会话。Telegram 图片和配文会下载到项目的 `telegram_media` 目录，并通过本地路径交给 Codex 桌面端读取。历史记录按以下形式显示：

```text
我：问题一

Codex：回答一

我：问题二

Codex：回答二
```

桥接不会把电脑端主动发送的新消息自动推送到 Telegram；只有通过 Telegram 发起的请求才会返回到同一个 Telegram 聊天。

## 运行文件

- `data/bridge.sqlite3`：Telegram 绑定和桥接诊断记录；
- `runtime/health.json`：启动健康状态；
- `logs/bridge.log`、`logs/bridge-error.log`：运行日志；
- `telegram_media/`：Telegram 图片缓存。

这些运行时文件已经加入 `.gitignore`，不应提交到公共仓库。

## 故障处理

- 如果提示服务已经运行，说明已有实例持有单实例锁，不会启动第二个 polling 实例。
- 如果健康检查失败，先确认 Codex 桌面端正在运行，再查看 `logs/bridge-error.log`。
- 如果 Telegram Token 失效，重新填写 BAT 顶部 Token；不要把 Token 写入 Python、README、日志或命令行参数。
- 关闭启动窗口不会停止后台服务。停止服务请结束 `bridge.py` 进程；重新双击 BAT 前先确认旧进程已退出。

## 许可证

本项目采用 MIT License。使用 Telegram Bot API 和 Codex 桌面本地接口时，请遵守相应服务条款。