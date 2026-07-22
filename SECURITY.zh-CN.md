# 安全策略

## 报告漏洞

如果你在 nanobot 中发现安全漏洞，请按以下方式报告：

1. **切勿**公开提交 GitHub issue
2. 在 GitHub 上创建私密安全公告（private security advisory），或联系仓库维护者（xubinrencs@gmail.com）
3. 包含以下内容：
   - 漏洞描述
   - 复现步骤
   - 潜在影响
   - 建议的修复方案（如有）

我们力争在 48 小时内回复安全报告。

## 安全最佳实践

### 1. API 密钥管理

**关键（CRITICAL）**：切勿将 API 密钥提交到版本控制。

```bash
# ✅ Good: Store in config file with restricted permissions
chmod 600 ~/.nanobot/config.json

# ❌ Bad: Hardcoding keys in code or committing them
```

**建议：**
- 将 API 密钥存储在 `~/.nanobot/config.json` 中，文件权限设为 `0600`
- 考虑对敏感密钥使用环境变量
- 生产部署时使用操作系统密钥环/凭证管理器
- 定期轮换 API 密钥
- 开发和生产使用不同的 API 密钥

### 2. 渠道访问控制

**重要（IMPORTANT）**：生产环境务必配置 `allowFrom` 列表。

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["123456789", "987654321"]
    },
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  }
}
```

**安全提示：**
- 在 `v0.1.4.post3` 及更早版本中，空的 `allowFrom` 允许所有用户访问。自 `v0.1.4.post4` 起，空的 `allowFrom` 默认拒绝所有访问 - 设置 `["*"]` 可显式允许所有人。
- 从 `@userinfobot` 获取你的 Telegram 用户 ID
- WhatsApp 使用带国家代码的完整电话号码
- 定期审查访问日志以发现未授权的访问尝试

### 3. Shell 命令执行

`exec` 工具可以执行 shell 命令。虽然危险命令模式已被拦截，但你仍应：

- ✅ **启用 bwrap 沙箱**（`"tools.exec.sandbox": "bwrap"`）以获得内核级隔离（仅限 Linux）
- ✅ 在代理日志中审查所有工具使用情况
- ✅ 了解代理正在运行哪些命令
- ✅ 使用权限受限的专用用户账户
- ✅ 切勿以 root 身份运行 nanobot
- ❌ 不要禁用安全检查
- ❌ 未经仔细审查，不要在含敏感数据的系统上运行

**Exec 沙箱（bwrap）：**

在 Linux 上，设置 `"tools.exec.sandbox": "bwrap"` 可将每条 shell 命令包装进 [bubblewrap](https://github.com/containers/bubblewrap) 沙箱。它利用 Linux 内核命名空间限制进程可见范围：

- 工作区目录 -> **读写**（代理正常工作）
- 媒体目录 -> **只读**（可读取上传的附件）
- 系统目录（`/usr`、`/bin`、`/lib`）-> **只读**（命令仍可正常运行）
- 配置文件和 API 密钥（`~/.nanobot/config.json`）-> **隐藏**（由 tmpfs 遮蔽）

需要安装 `bwrap`（`apt install bubblewrap`）。官方 Docker 镜像中已预装。**在 macOS 或 Windows 上不可用** - bubblewrap 依赖 Linux 内核命名空间。

启用沙箱还会自动为文件工具激活 `restrictToWorkspace`。

**已拦截的模式：**
- `rm -rf /` - 根文件系统删除
- Fork bomb（fork 炸弹）
- 文件系统格式化（`mkfs.*`）
- 裸磁盘写入
- 其他破坏性操作

### 4. 文件系统访问

文件操作已具备路径穿越（path traversal）防护，但：

- ✅ 启用 `restrictToWorkspace` 或 bwrap 沙箱以限制文件访问
- ✅ 使用专用用户账户运行 nanobot
- ✅ 使用文件系统权限保护敏感目录
- ✅ 定期审计日志中的文件操作
- ❌ 不要授予对敏感文件的不受限访问

### 5. 网络安全

**API 调用：**
- 所有外部 API 调用默认使用 HTTPS
- 已配置超时以防止请求挂起
- 如有需要，可考虑使用防火墙限制出站连接

**WhatsApp Bridge：**
- 该 bridge 绑定到 `127.0.0.1:3001`（仅本地主机，外部网络不可访问）
- 在配置中设置 `bridgeToken` 以启用 Python 与 Node.js 之间的共享密钥认证
- 妥善保护 `~/.nanobot/whatsapp-auth` 中的认证数据（权限模式 0700）

### 6. 依赖安全

**关键（Critical）**：保持依赖更新！

```bash
# Check for vulnerable dependencies
pip install pip-audit
pip-audit

# Update to latest secure versions
pip install --upgrade nanobot-ai
```

对于 Node.js 依赖（WhatsApp bridge）：
```bash
cd bridge
npm audit
npm audit fix
```

**重要提示：**
- 将 `litellm` 更新到最新版本以获取安全修复
- 我们已将 `ws` 更新至 `>=8.17.1` 以修复 DoS 漏洞
- 定期运行 `pip-audit` 或 `npm audit`
- 订阅 nanobot 及其依赖的安全公告

### 7. 生产部署

用于生产环境时：

1. **隔离环境**
   ```bash
   # Run in a container or VM
   docker run --rm -it python:3.11
   pip install nanobot-ai
   ```

2. **使用专用用户**
   ```bash
   sudo useradd -m -s /bin/bash nanobot
   sudo -u nanobot nanobot gateway
   ```

3. **设置正确的权限**
   ```bash
   chmod 700 ~/.nanobot
   chmod 600 ~/.nanobot/config.json
   chmod 700 ~/.nanobot/whatsapp-auth
   ```

4. **启用日志**
   ```bash
   # Configure log monitoring
   tail -f ~/.nanobot/logs/nanobot.log
   ```

5. **使用速率限制**
   - 在你的 API provider 上配置速率限制
   - 监控用量以发现异常
   - 在 LLM API 上设置支出上限

6. **定期更新**
   ```bash
   # Check for updates weekly
   pip install --upgrade nanobot-ai
   ```

### 8. 开发环境与生产环境

**开发环境：**
- 使用独立的 API 密钥
- 使用非敏感数据进行测试
- 启用详细日志
- 使用测试用 Telegram bot

**生产环境：**
- 使用带支出上限的专用 API 密钥
- 限制文件系统访问
- 启用审计日志
- 定期进行安全审查
- 监控异常活动

### 9. 数据隐私

- **日志可能包含敏感信息** - 妥善保护日志文件
- **LLM provider 可以看到你的 prompt** - 查阅其隐私政策
- **聊天记录存储在本地** - 保护 `~/.nanobot` 目录
- **API 密钥以明文存储** - 生产环境请使用操作系统密钥环

### 10. 事件响应

如果你怀疑发生安全入侵：

1. **立即吊销已泄露的 API 密钥**
2. **审查日志中的未授权访问**
   ```bash
   grep "Access denied" ~/.nanobot/logs/nanobot.log
   ```
3. **检查意外的文件修改**
4. **轮换所有凭证**
5. **更新到最新版本**
6. **向维护者报告该事件**

## 安全特性

### 内置安全控制

✅ **输入校验**
- 文件操作具备路径穿越防护
- 危险命令模式检测
- HTTP 请求的输入长度限制

✅ **认证**
- 基于允许列表（allow-list）的访问控制 - 在 `v0.1.4.post3` 及更早版本中，空的 `allowFrom` 允许所有访问；自 `v0.1.4.post4` 起拒绝所有访问（`["*"]` 显式允许所有）
- 失败认证尝试记录

✅ **资源保护**
- 命令执行超时（默认 60 秒）
- 输出截断（10KB 限制）
- HTTP 请求超时（10-30 秒）

✅ **安全通信**
- 所有外部 API 调用使用 HTTPS
- Telegram API 使用 TLS
- WhatsApp bridge：仅绑定本地主机 + 可选的 token 认证

## 已知限制

⚠️ **当前安全限制：**

1. **无速率限制** - 用户可以发送无限消息（如有需要请自行添加）
2. **明文配置** - API 密钥以明文存储（生产环境请使用密钥环）
3. **无会话管理** - 无自动会话过期机制
4. **有限的命令过滤** - 仅拦截明显的危险模式（在 Linux 上启用 bwrap 沙箱可获得内核级隔离）
5. **无审计追踪** - 安全事件日志有限（按需增强）

## 安全清单

部署 nanobot 之前：

- [ ] API 密钥已安全存储（不在代码中）
- [ ] 配置文件权限已设为 0600
- [ ] 所有渠道已配置 `allowFrom` 列表
- [ ] 以非 root 用户运行
- [ ] Linux 部署上已启用 exec 沙箱（`"tools.exec.sandbox": "bwrap"`）
- [ ] 文件系统权限已适当限制
- [ ] 依赖已更新到最新安全版本
- [ ] 已监控日志中的安全事件
- [ ] API provider 上已配置速率限制
- [ ] 已制定备份与灾难恢复计划
- [ ] 已对自定义技能/工具进行安全审查

## 更新

**最后更新**：2026-04-05

如需获取最新安全更新与公告，请查看：
- GitHub 安全公告：https://github.com/HKUDS/nanobot/security/advisories
- 发行说明：https://github.com/HKUDS/nanobot/releases

## 许可证

详情见 LICENSE 文件。
