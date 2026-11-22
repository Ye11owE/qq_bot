# 🤖 QQ AI Agent Bot
（此项目借助千问+codex制作生成）
基于 **NoneBot2 + OneBot v11 + OpenAI API** 的 QQ 智能聊天机器人，支持多轮对话、工具调用、文件收发、图片理解等能力。

## ✨ 功能特性

- 💬 **智能对话**：接入 OpenAI 兼容 API，支持多轮上下文会话
- 🔧 **Agent 工具调用**：网页搜索、网页浏览、代码执行、文件读写
- 📁 **文件收发**：接收用户文件 → AI 处理 → 自动回传结果
- 🖼️ **图片理解**：支持图片翻译、题目解析等视觉任务
- 🛡️ **安全防护**：关键词过滤、频率限制、消息去重、夜间静默
- 📢 **多渠道告警**：支持钉钉/飞书/企微/Bark/PushPlus/Server酱/邮件
- 🔍 **Watchdog 守护**：自动检测异常、智能重启、认证备份

## 📁 项目结构

```
.
├── bot.py                 # NoneBot2 入口
├── plugins/
│   ├── chat.py            # 核心聊天插件（Agent + 会话管理）
│   └── agent_tools.py     # Agent 工具集定义与实现
├── mail_alert.py          # 邮件告警模块
├── notify.sh              # 多渠道通知脚本
├── watchdog.sh            # 进程守护与健康检查
├── .env.example           # 环境变量模板
├── .gitignore             # Git 忽略规则
├── requirements.txt       # Python 依赖
└── README.md              # 本文件
```

## 🚀 快速开始

### 1. 环境准备

- Python 3.10+
- [NapCat](https://github.com/NapNeko/NapCatQQ) 或其他 OneBot v11 协议端

### 2. 安装依赖

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env，填入你的 AI API Key 和其他可选配置
```

`CHAT_TEXT_MODEL` 和 `CHAT_VISION_MODEL` 使用你的 API 服务实际支持的模型名。视觉模型留空时沿用文本模型。

### 4. 连接 OneBot 协议端

启动机器人后，在 NapCat 的 OneBot v11 设置中添加反向 WebSocket 连接，地址填写：

```text
ws://<机器人服务器地址>:8096/onebot/v11/ws
```

机器人与 NapCat 在同一台机器时，可以将地址主机名设为 `127.0.0.1`。如果配置了 `ONEBOT_V11_ACCESS_TOKEN`，NapCat 端也要填写相同的 token。

### 5. 启动

```bash
python bot.py
```

### 6. 生产部署（推荐）

使用 systemd 管理 + watchdog 守护：

```bash
# 按自己的服务配置启动 NoneBot 和 NapCat
sudo systemctl start qqbot-nonebot.service qqbot-napcat.service

# watchdog 默认项目目录 /opt/qqbot，Python 环境 /opt/qqbot_venv
./watchdog.sh &
```

如果使用其他目录或 systemd 服务名，启动 watchdog 前设置 `QQBOT_DIR`、`QQBOT_PYTHON`、`QQBOT_NAPCAT_SERVICE`、`QQBOT_NONEBOT_SERVICE` 和 `QQBOT_CONFIG_DIR`。`notify.sh` 通过 `QQBOT_DIR` 查找 `.env.notify`。

## ⚙️ 配置说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `CHAT_API_KEY` | ✅ | AI API 密钥（OpenAI / DeepSeek / 通义千问等） |
| `CHAT_API_BASE_URL` | ❌ | API 地址，默认 `https://api.openai.com/v1` |
| `CHAT_TEXT_MODEL` | ❌ | 文本模型，默认 `gpt-4o-mini` |
| `CHAT_VISION_MODEL` | ❌ | 图片理解模型，留空时跟随文本模型 |
| `ONEBOT_V11_ACCESS_TOKEN` | ❌ | OneBot v11 WebSocket 连接 token，需与 NapCat 配置一致 |
| `QQBOT_FILE_WORK_DIR` | ❌ | 接收及生成文件目录，默认 `/tmp/qqbot_files` |
| `QQBOT_REPLY_DELAY_MIN` | ❌ | 回复前最短等待秒数，默认 `3` |
| `QQBOT_REPLY_DELAY_MAX` | ❌ | 回复前最长等待秒数，默认 `8` |
| `QQBOT_SEGMENT_DELAY_MIN` | ❌ | 分段消息间最短等待秒数，默认 `4` |
| `QQBOT_SEGMENT_DELAY_MAX` | ❌ | 分段消息间最长等待秒数，默认 `10` |
| `QQBOT_SESSION_TIMEOUT` | ❌ | 对话历史超时秒数，默认 `3600` |
| `QQBOT_ALLOWED_DIRS` | ❌ | Agent 额外可读写的目录，多个目录用系统路径分隔符，默认包含项目目录、`/tmp` 和 `/home` |
| `ALERT_SMTP_USER` | ❌ | 邮件告警发件人邮箱 |
| `ALERT_SMTP_PASS` | ❌ | QQ 邮箱 SMTP 授权码 |
| `ALERT_TO_EMAIL` | ❌ | 告警接收邮箱 |

通知渠道（在 `.env.notify` 中配置）：钉钉、飞书、企业微信、Bark、PushPlus、Server酱、自定义 Webhook。

## ℹ️ 运行说明

- `.env` 由机器人入口和独立邮件告警脚本分别读取，不需要额外导出环境变量。
- 私聊会话按 QQ 用户隔离，群聊会话按群号和用户隔离。
- 文件大小上限为 5 MiB，文件工作目录可通过 `QQBOT_FILE_WORK_DIR` 调整。
- 网页浏览工具首次使用需要安装 Playwright Chromium。
- `run_code` 在独立 Python 进程中执行，单次运行最长 15 秒，超时会自动停止。

## 📄 License

MIT License
