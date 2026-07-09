# MailPush Development Progress

本地项目地址：`C:\Users\Admin\Documents\mailpush`

远程仓库：`https://github.com/fkdls112/mailpush.git`

记录时间：2026-07-05

## 当前方向

把 MailPush 从个人 Hermes 邮件推送脚本，逐步升级为通用邮件事件网关和 Agent/平台连接器框架。

## ✅ 已完成 (2026-07-05)

### Delivery Adapter 框架

- ✅ `mailpush/delivery/base.py` — DeliveryAdapter 抽象基类 + resolve_value 配置解析
- ✅ `mailpush/delivery/hermes.py` — Hermes CLI/API 双模式 adapter
- ✅ `mailpush/delivery/webhook.py` — HTTPS webhook adapter (HMAC 签名)
- ✅ `mailpush/delivery/http.py` — 通用 HTTP adapter (可配置 method/headers/payload)
- ✅ `mailpush/delivery/command.py` — 本地命令 adapter
- ✅ `mailpush/delivery/openclaw.py` — OpenClaw gateway/message/command 三模式

### Dispatcher (调度核心)

- ✅ `mailpush/delivery/dispatcher.py` — 完整的事件分发管线:
  - `render_event()`: MailEvent → 人类可读消息
  - `_build_adapters()`: 从 config 构建 adapter 实例（新 deliveries + 旧 delivery_targets 自动转换）
  - `_match_routes()`: 路由规则匹配（account/sender/subject/priority/tags）
  - `dispatch_event()`: 渲染 → 路由 → 发送，完整管线
  - `dispatch_message()` / `dispatch_notification()`: 纯文本消息广播
  - `list_configured()`: 安全列出所有 adapter（脱敏）

### 服务层集成

- ✅ `mailpush/server.py` — 完成 dispatcher 接入:
  - `on_email` 回调改用 `dispatch_event()`
  - `/api/notify` 改用 `dispatch_notification()`，返回详细结果
  - 新增 `/api/delivery` — 列出所有 adapter
  - 新增 `/api/delivery/test` — 发送测试消息
  - `/api/state` 增加 `deliveries` 字段
  - 完全向后兼容旧 `delivery_targets` 配置

### 配置升级

- ✅ `mailpush/config.py` — 新增 `deliveries` 和 `routes` 字段，默认配置包含示例

## 配置示例

```json
{
  "delivery_targets": ["wechat"],
  "deliveries": [
    {
      "name": "hermes-wechat",
      "type": "hermes",
      "config": {"mode": "cli", "target": "wechat"}
    },
    {
      "name": "alert-webhook",
      "type": "webhook",
      "config": {
        "url": "https://hooks.example.com/alerts",
        "secret": "env:WEBHOOK_SECRET"
      }
    }
  ],
  "routes": [
    {
      "match": {"account": ["QQ"], "subject_contains": "告警"},
      "adapters": ["alert-webhook"]
    }
  ]
}
```

## 兼容性

- 旧 `delivery_targets: ["wechat"]` 自动转换为 Hermes CLI adapter
- 混合使用新旧配置时自动去重
- API 响应格式保持兼容，新增字段为增量

## 验证

- ✅ Python 编译检查通过（全部 .py 文件）
- ✅ NAS 备份完成 (27 files → /nas-hermes/mailpush-v9/)
- ✅ Windows 推送完成 (MD5 校验通过)

## 下一步建议

- 在 Windows 上安装依赖后运行 `mailpush` CLI 测试
- 更新 README.md 添加 delivery 配置文档
- 推送至 GitHub
