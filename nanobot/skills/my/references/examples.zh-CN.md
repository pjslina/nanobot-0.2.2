# My 工具 - 实用示例

展示何时以及如何有效使用 my 工具的具体场景。

## 诊断

### "为什么你不能搜索网页？"
```
-> my(action="check", key="web_config.enable")
  -> False
-> "网页搜索已禁用。在你的配置中添加 web.enable: true 以启用它。"
```

### "你为什么停下来了？"
```
-> my(action="check", key="max_iterations")
  -> 40
-> my(action="check", key="_last_usage")
  -> {"prompt_tokens": 62000, "completion_tokens": 3000}
-> "我达到了迭代上限（40）。任务比较复杂。我可以询问用户是否想要提高它。"
```

### "你正在运行什么模型？"
```
-> my(action="check", key="model")
  -> 'anthropic/claude-sonnet-4-20250514'
-> my(action="check", key="model_preset")
  -> 'deep'
```

## 自适应行为

### 大型代码库分析
```
-> my(action="check")
  -> context_window_tokens: 200000
-> my(action="set", key="context_window_tokens", value=262144)
  -> "已设置 context_window_tokens = 262144（原为 200000）"
-> "我已经扩展了上下文窗口以处理这个大型代码库。"
```

### 切换到已配置的模型预设
```
-> my(action="set", key="model_preset", value="fast")
  -> "已设置 model_preset = 'fast'（原为 'deep'）；model 现在为 'openai/gpt-4.1-mini'"
-> "已为这些批量任务切换到 fast 预设。"
```

### 当不存在预设时切换到原始模型
```
-> my(action="set", key="model", value="anthropic/claude-haiku-4-5-20251001")
  -> "已设置 model = 'anthropic/claude-haiku-4-5-20251001'（原为 'anthropic/claude-sonnet-4-20250514'）"
-> "已为这些批量任务切换到更快的模型。"
```

## 跨回合记忆

### 记住用户偏好
```
# 回合 1：用户说"保持简短"
-> my(action="set", key="user_style", value="concise")
  -> "已设置 scratchpad.user_style = 'concise'"

# 回合 3：新话题
-> my(action="check", key="user_style")
  -> 'concise'
  （相应调整响应风格）
```

### 跟踪项目上下文
```
-> my(action="set", key="active_branch", value="feat/auth")
-> my(action="set", key="test_framework", value="pytest")
-> my(action="set", key="has_docker", value=true)
```

## 预算感知

### 关注 Token 的行为
```
-> my(action="check", key="_last_usage")
  -> {"prompt_tokens": 58000, "completion_tokens": 12000}
-> "我已经消耗了约 70k token。我会让剩余的回复保持聚焦。"
```
