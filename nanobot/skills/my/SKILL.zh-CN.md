---
name: my
description: 检查并设置 agent 自身的运行时状态（模型、迭代次数、上下文窗口、token 使用量、web 配置）。在以下情况使用：诊断某事为何不工作时（"为什么你无法搜索 web？"、"为什么你停止了？"）、在复杂任务前检查资源限制、为长任务或简单任务调整配置，或跨多轮记住用户偏好。当用户询问你正在运行什么模型、已使用多少 token、或你的设置是什么时，也应使用。
always: true
---

# 自我感知

## 如何使用

1. 从以下分类中**识别情境**
2. 使用合适的 action **调用 my 工具**
3. **如果是 set**，在更改有影响的设置（model、iterations）前警告用户
4. **获取详细示例**，请阅读 [references/examples.md](references/examples.md)

## 何时检查

<rule>
**先诊断再解释。** 当某事不工作时，先检查你的状态。
</rule>

<rule>
**在复杂任务前检查预算。** 在承诺前了解你的限制。
</rule>

<rule>
**跨轮次回忆。** 将偏好存储在你的 scratchpad 中，稍后读回。
</rule>

## 何时设置

<rule>
**仅在收益明确且用户知情时设置。** 更改 model 前先警告。
</rule>

| 情境 | 命令 |
|-----------|---------|
| 大型代码库分析 | `my(action="set", key="context_window_tokens", value=262144)` |
| 切换到命名模型预设 | `my(action="set", key="model_preset", value="<preset-name>")` |
| 没有预设的重复简单任务 | `my(action="set", key="model", value="<fast-model>")` |
| 长多步任务 | `my(action="set", key="max_iterations", value=80)` |

**权衡：** 倾向于稳定。仅在默认值确实不足时设置。

## 反模式

<rule>
**不要每轮都检查。** 会消耗一次工具调用。在需要信息时使用，而非反射性地使用。
</rule>

<rule>
**不要存储敏感数据。** scratchpad 中不要放 API 密钥、密码或 token。
</rule>

<rule>
**不要设置 workspace。** 不会更新文件工具的边界 - 不会生效。
</rule>

## 约束

- 所有修改仅在内存中 - 重启会重置一切
- 对于已配置的模型选择，优先使用 `model_preset`。直接更改 `model` 会清除当前激活的预设，应仅在没有预设时使用。
- 受保护参数有类型/范围校验：`max_iterations` (1–100)、`context_window_tokens` (4096–1M)、`model` (非空 str)
- 如果 `tools.my.allow_set` 为 false，则仅可检查

## 相关工具

| 需求 | 使用 | 是否持久化？ |
|------|-----|-----------|
| 每会话临时状态 | `my(action="set", key="...", value=...)` | 否 |
| 长期事实 | Memory skill (`MEMORY.md`, `USER.md`) | 是 |
| 永久配置更改 | 编辑配置文件 | 是 |

**经验法则：** 明天还要用？用 Memory。仅本轮？用 My。
