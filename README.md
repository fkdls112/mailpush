# MailPush

**IMAP IDLE 实时邮件推送 → REST API → AI Agent / Webhook / 微信 / Telegram**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688)](https://fastapi.tiangolo.com/)

MailPush 是一个轻量级多账户邮件监控系统，通过 IMAP IDLE 实时监听收件箱，支持翻译、摘要提取、过滤，并通过 REST API / Webhook 分发给 AI Agent 或消息平台。

---

## 特性

- 🔔 **IMAP IDLE 实时监听** — 多账户并发，到件秒级感知
- 🔒 **API Token 认证** — 所有 `/api/*` 端点强制 `X-API-Token`
- 🪝 **Webhook 推送** — 强制 HTTPS + TLS 证书验证，支持 HMAC 签名
- 🌐 **多语言翻译** — MyMemory 免费 API，传输前自动脱敏（IP/邮箱/密码模糊化）
- 📊 **Dashboard** — 暗色主题 Web UI，邮件/账户/Webhook/配置管理
- 🧠 **AI 摘要** — 自动提取 IP、金额、验证码、URL
- 📬 **多平台分发** — REST API 输出，可桥接微信/Telegram/Discord 等
- 🏗️ **模块化架构** — 15 个源文件，清晰的职责分离
- 🔄 **断线重连回溯** — 服务中断后自动处理遗漏邮件

---

## 架构

```
┌──────────────────────────────────────────────────┐
│                  IMAP Servers                      │
│   imap.qq.com    imap.gmail.com    ...            │
└──────┬──────────────────────┬─────────────────────┘
       │  IMAP IDLE           │  IMAP IDLE
       ▼                      ▼
┌──────────────────────────────────────────────────┐
│              MailPush Core                         │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │  imap.py │→│ filter.py│→│  processor.py     │ │
│  │ (监听)   │  │ (黑白名单)│  │ (正文提取/摘要)   │ │
│  └─────────┘  └──────────┘  └──────┬───────────┘ │
│                     ┌──────────────┼───────────┐  │
│                     ▼              ▼           ▼  │
│              ┌──────────┐  ┌──────────┐  ┌──────┐ │
│              │translator│  │summarizer│  │smtp  │ │
│              │ (翻译)    │  │ (AI摘要) │  │(回复)│ │
│              └──────────┘  └──────────┘  └──────┘ │
│                         │                         │
│                    ┌────▼─────┐                    │
│                    │ server.py│                    │
│                    │ (FastAPI)│                    │
│                    └────┬─────┘                    │
└─────────────────────────┼──────────────────────────┘
                          │ REST API (:8080)
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────────────┐
   │Dashboard │   │ Webhook  │   │ hermes send       │
   │  (Web UI)│   │ (HTTPS)  │   │ → 微信 + Telegram │
   └──────────┘   └──────────┘   └──────────────────┘
                                         ▲
                          ┌──────────────┘
                          │ POST /api/notify
                          │ (外部程序通用通知)
```

---

## 快速开始

### 安装

```bash
cd mailpush
pip install -e .
```

### 配置

```bash
# 创建配置模板
mailpush config init

# 编辑 ~/.config/mailpush/config.json
```

配置示例：

```json
{
  "server": {"host": "127.0.0.1", "port": 8080},
  "api_token": "your-random-token-here",
  "delivery_targets": [
    "weixin:OPENID@im.wechat",
    "telegram:CHAT_ID"
  ],
  "translate": false,
  "summary": true,
  "merge_batch": false,
  "accounts": [
    {
      "name": "MyMail",
      "host": "imap.example.com",
      "port": 993,
      "username": "user@example.com",
      "password": "app-password-or-password",
      "smtp_host": "smtp.example.com",
      "smtp_port": 587,
      "smtp_username": "user@example.com",
      "smtp_password": "app-password"
    }
  ],
  "filters": {
    "whitelist": [],
    "blacklist": []
  }
}
```

> `delivery_targets` 支持多平台：`weixin:`, `telegram:`, `dingtalk:`, `wecom:`, `discord:` 等。填几个推几个，留空 `[]` 则仅保存到 API 缓存不主动推送。

### 启动

```bash
# 直接启动
mailpush serve --host 127.0.0.1 --port 8080

# systemd 服务（推荐）
systemctl --user enable --now mailpush-api.service
```

### CLI 命令

```bash
mailpush serve           # 启动服务
mailpush status          # 查看账户状态
mailpush test <account>  # 测试 IMAP 连接
mailpush accounts list   # 列出账户
mailpush accounts add    # 添加账户
mailpush webhook add     # 注册 Webhook
```

---

## API 端点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|:--:|
| GET | `/api/health` | 健康检查（免认证） | - |
| GET | `/api/emails` | 邮件列表（支持 `?since=&account=&limit=`） | ✅ |
| GET | `/api/accounts` | 账户列表 + IMAP 连接状态 | ✅ |
| POST | `/api/accounts` | 添加账户 | ✅ |
| DELETE | `/api/accounts/{name}` | 删除账户 | ✅ |
| GET | `/api/webhooks` | Webhook 列表 | ✅ |
| POST | `/api/webhooks` | 注册 Webhook（强制 HTTPS） | ✅ |
| DELETE | `/api/webhooks/{id}` | 删除 Webhook | ✅ |
| POST | `/api/reply` | SMTP 回复邮件 | ✅ |
| GET | `/api/config` | 当前配置（密码已脱敏） | ✅ |
| POST | `/api/config` | 更新配置 | ✅ |
| GET | `/api/state` | 完整服务状态 | ✅ |
| POST | `/api/notify` | 通用通知（推送到 delivery_targets） | ✅ |
| GET | `/openapi.json` | OpenAPI/Swagger 文档 | - |
| GET | `/` | Dashboard | - |

> 认证方式：`X-API-Token: <your-token>`

---

## 安全设计

| 层级 | 措施 |
|------|------|
| **文件系统** | `config.json` 自动 `chmod 600`，原子写入（tmpfile + rename） |
| **API** | `X-API-Token` 强制认证，`/api/health` 免认证 |
| **Webhook** | 强制 HTTPS + TLS 证书验证（`verify=True`），拒绝 HTTP |
| **翻译** | 传输前脱敏 — IP → `[IP]`，邮箱 → `[EMAIL]`，密码/Token 字段 → `[***]` |
| **配置** | 凭据外部化，不写入源码，`.gitignore` 排除所有 JSON |

---

## 推送模式

MailPush 支持两种邮件投递模式，可在运行时按需切换。

### 模式 A：实时推送（hermes send，默认）

邮件到达后，MailPush 直接调用 `hermes send` 并发推送到 `delivery_targets` 配置的所有平台：

```
新邮件 → IMAP IDLE (秒级) → hermes send → 微信 + Telegram + ...
```

**优点**：延迟最低（秒级），代码简洁。

**已知限制 — 微信限流**：

`hermes send` 到微信走的是 iLink CLI 网关，内置了一个 30 秒冷却计数器。该计数器在某些情况下**不会自动清除**，导致限流窗口卡死后所有后续推送都被丢弃。此限流独立于 Agent 聊天通道，微信聊天本身不受影响。

**症状**：服务启动后前几条能收到，之后静默丢失。`journalctl --user -u mailpush-api` 中无异常日志（hermes 返回 exit 0 但消息未抵达）。

**解决方案**：

- **重启 MailPush**：`systemctl --user restart mailpush-api`（重启会恢复，因为 cold start 后限流计数器重新初始化）
- **改用模式 B**：如果频繁出问题，切换到 cron 轮询模式（见下文）

> ⚠️ 如果你的 `delivery_targets` 只含 Telegram / DingTalk 等非微信平台，则不受此限流影响。

### 模式 B：Cron 轮询（备选方案）

当微信限流影响实时推送时，可退回到 cron 轮询模式。在该模式下 MailPush **不主动推送**，而是将新邮件缓存在 API 中，由外部 cron 脚本定期拉取：

```
新邮件 → IMAP IDLE → API 缓存 → cron (每分钟) → hermes send → 微信
```

**切换步骤**：

**1. 清空 delivery_targets**（停用实时推送）：

```json
// ~/.config/mailpush/config.json
{
  "delivery_targets": []
}
```

**2. 配置 cron 拉取脚本**：

```bash
# crontab -e
* * * * * /home/user/.hermes/scripts/mailpush-cron.sh
```

**3. 脚本示例**（`~/.hermes/scripts/mailpush-cron.sh`）：

```bash
#!/bin/bash
LAST_TS_FILE="$HOME/.cache/mailpush_last_ts"
API_TOKEN="your-token-here"

LAST_TS=$(cat "$LAST_TS_FILE" 2>/dev/null || echo "0")

RESP=$(curl -s -m 10 \
  -H "X-API-Token: $API_TOKEN" \
  "http://127.0.0.1:8080/api/emails?since=${LAST_TS}&limit=5")

echo "$RESP" | python3 -c "
import json,sys,os,subprocess
data = json.load(sys.stdin)
emails = data.get('emails', [])
if not emails:
    sys.exit(0)
for e in emails:
    msg = f'📬 {e[\"account\"]} — {e[\"sender\"]} — {e[\"subject\"]}'
    body = e.get('body', '')[:200]
    if body:
        msg += f'\n  {body}'
    subprocess.run(['hermes', 'send', '--to', 'weixin:OPENID@im.wechat', msg])
# Save latest timestamp
ts = emails[-1].get('received_at', 0)
with open(os.path.expanduser('~/.cache/mailpush_last_ts'), 'w') as f:
    f.write(str(ts))
print(f'{len(emails)} emails sent, last_ts={ts}')
" || echo "mailpush cron failed"
```

**缺点**：邮件从抵达到你收到通知，最多延迟 **60 秒**（cron 最小粒度为 1 分钟）。邮件频率越低延迟越明显。

### 外部程序通知：`POST /api/notify`

不仅是邮件，**任何外部程序**都可以通过此端点直接给你发消息：

```bash
curl -X POST http://127.0.0.1:8080/api/notify \
  -H "X-API-Token: your-token" \
  -H "Content-Type: application/json" \
  -d '{"message": "备份完成: 2026-07-04 02:30"}'
```

→ 消息直推到 `delivery_targets` 配置的所有平台。

| 场景 | 示例 |
|------|------|
| CI/CD 结果 | Jenkins / GitHub Actions 构建完毕 |
| cron 任务 | 定时备份、日志清理完成提醒 |
| 系统监控 | 磁盘 > 90%、服务 crash、CPU 异常 |
| 自定义程序 | 任何能发 HTTP 请求的脚本 |

> 注意：`/api/notify` 也通过 `hermes send` 发送，所以在微信限流卡死期间推送同样不可靠。如需稳定通知，建议 Telegram 为主 + 微信备份。

---

## 部署案例：Hermes Agent 集成

> 以下为生产环境真实部署案例，已脱敏处理。

### 场景

在一个运行 Hermes AI Agent 的 Debian 服务器上部署 MailPush，监控 4 个邮箱（QQ + 3×Gmail），通过 `hermes send` 将新邮件实时推送到**微信 + Telegram 双平台**。同时对外暴露 `/api/notify` 端点，允许 CI/CD、cron 任务等外部程序直接通知用户。

### 环境

| 组件 | 配置 |
|------|------|
| OS | Debian 12 (Bookworm) |
| Python | 3.11 |
| 服务管理 | systemd --user |
| 邮箱 | QQ邮箱 ×1, Gmail ×3 |
| 翻译 | MyMemory（免费，无需代理） |
| 投递 | `hermes send` 实时推送（微信 + Telegram 双平台并发） |
| 备用 | cron 轮询（微信限流时降级，见「推送模式 → 模式 B」） |

### 服务配置

```ini
# ~/.config/systemd/user/mailpush-api.service
[Unit]
Description=MailPush API Server — IMAP IDLE + REST API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m mailpush serve --host 127.0.0.1 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

### 投递配置（delivery_targets）

在 `config.json` 中配置推送目标，支持多平台并发：

```json
{
  "delivery_targets": [
    "weixin:OPENID@im.wechat",
    "telegram:5941885113"
  ]
}
```

邮件到达后格式如下：

```
📬 QQ — sender@example.com — 邮件主题
  📝 IP: 192.168.1.1 / 10.0.0.1
  📝 ¥299.00
  📎 report.pdf
```

### 运维经验

1. **IMAP IDLE 超时断开** — QQ 邮箱 IDLE 约 20-30 分钟无活动后断开，需在重连时执行 `uid_search` 回溯遗漏邮件（v1.0.0 已内置）
2. **aioimaplib 参数陷阱** — `imap.uid('fetch', str(uid).encode(), ...)` 中的 `.encode()` 会导致响应截断，应传纯字符串
3. **bytearray 类型匹配** — aioimaplib 返回混合 `bytes`/`bytearray`，提取函数需同时匹配两种类型
4. **安全加固后验证** — 添加 API Token 后务必同步更新所有调用方（cron 脚本、Dashboard JS）
5. **Dashboard 缓存** — HTML 注入 Token 后，需硬刷新（`Page.reload(ignoreCache=true)`）清除旧缓存
6. **`since` 过滤用 `>=` 导致重复推送** — 投递脚本保存的 `last_ts` 与下一条邮件时间戳完全相同时，`>=` 会永远返回同一封邮件，应使用 `>` 严格大于

### 关键指标

- 连接数：4 账户并发 IMAP IDLE
- 内存占用：~48 MB
- API 延迟：< 5ms（localhost）
- 邮件检测延迟：实时模式秒级，cron 轮询模式最长 60s
- 安全审计：0 高风险，0 中风险（所有已知问题已修复）

---

## 项目结构

```
mailpush/
├── mailpush/
│   ├── __init__.py
│   ├── __main__.py      # python -m mailpush
│   ├── cli.py           # CLI 入口（click/argparse）
│   ├── config.py        # 配置管理（加载/保存/默认值）
│   ├── imap.py          # IMAP IDLE 监听 + 回溯
│   ├── processor.py     # 邮件正文提取
│   ├── filter.py        # 黑白名单过滤
│   ├── translator.py    # 翻译（MyMemory + 脱敏）
│   ├── summarizer.py    # AI 规则摘要
│   ├── webhook.py       # Webhook 分发 + HMAC 签名
│   ├── smtp.py          # SMTP 回复
│   ├── server.py        # FastAPI 应用（认证/路由/Dashboard）
│   ├── models.py        # Pydantic 模型
│   └── static/
│       ├── index.html   # Dashboard HTML
│       ├── app.js       # Dashboard JS
│       └── style.css    # 暗色主题
├── pyproject.toml
├── README.md
├── LICENSE
└── .gitignore
```

---

## License

MIT © 2026
