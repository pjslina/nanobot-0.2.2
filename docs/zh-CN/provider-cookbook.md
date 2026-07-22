# Provider 实用手册

本页面适用于你已知道要连接什么、且需要一个可直接粘贴的配置的场景。每个配方都展示了要设置什么、要运行什么，以及失败通常意味着什么。

如果这是你首次安装且对终端命令不熟悉，请先阅读 [`start-without-technical-background.md`](./start-without-technical-background.md)。如果你想要逐字段的解释，请先阅读 [`providers.md`](./providers.md)，再阅读 [`configuration.md#providers`](./configuration.md#providers)。

下面大多数示例都是需要合并进 `~/.nanobot/config.json` 的代码片段。保留你仍然需要的任何现有章节，并且仅在自家机器上用环境变量引用或真实值替换 `${OPENROUTER_API_KEY}` 等占位密钥。

这些配方是示例，不是排名。请选择与你已经打算使用的凭据、端点和模型 ID 相匹配的配方。

## 选择配方

将配方与你已有的凭据或端点相匹配：

| 你拥有的内容 | 配方 | 必须匹配项 |
|---|---|---|
| 一个网关密钥，以及包含模型家族路径的模型 ID，例如 `provider/model-name` | [OpenRouter 网关](#recipe-openrouter-gateway) | API 密钥、provider 配置键、预设 provider 以及网关模型 ID |
| 一个 OpenAI 平台 API 密钥和 OpenAI 模型 ID | [OpenAI 直连](#recipe-openai-direct) | `OPENAI_API_KEY`、`provider: "openai"`，以及该账户可用的 OpenAI 模型 |
| 一个 Anthropic API 密钥和 Anthropic 模型 ID | [Anthropic 直连](#recipe-anthropic-direct) | `ANTHROPIC_API_KEY`、`provider: "anthropic"`，以及非网关模型 ID |
| 一个 OpenAI 兼容的 `/v1` 端点，且不是已命名的 nanobot provider | [自定义 OpenAI 兼容 Provider](#recipe-custom-openai-compatible-provider) | `apiBase`、可选的 API 密钥，以及该端点提供的模型 ID |
| Ollama 已在本地运行 | [Ollama 本地模型](#recipe-ollama-local-model) | Ollama `apiBase`、已拉取的模型名称，以及本地服务器可用性 |
| vLLM、LM Studio 或其他本地 OpenAI 兼容服务器 | [vLLM 或 LM Studio](#recipe-vllm-or-lm-studio) | 本地 `/v1` 基础 URL、任何所需密钥，以及提供的模型名称 |
| 一个主模型加上一个或多个备份 | [回退预设](#recipe-fallback-presets) | `modelPresets` 中命名的预设，从 `agents.defaults.fallbackModels` 引用 |
| 一个可用的 agent 和一个 Langfuse 项目 | [Langfuse 追踪](#recipe-langfuse-tracing) | 在启动 nanobot 的同一进程环境中设置 Langfuse 环境变量 |

## 如何使用配方

1. 安装 nanobot 并运行一次 `nanobot onboard`，使 `~/.nanobot/config.json` 存在。如果你更喜欢交互提示而非手动编辑 JSON，请使用 `nanobot onboard --wizard`。
2. 尽可能将密钥放入环境变量中。
3. 将配方片段合并进 `~/.nanobot/config.json`。
4. 运行 `nanobot status`。
5. 运行 `nanobot agent -m "Hello!"`。
6. 如果 CLI 可用，则连接 WebUI、网关或聊天应用。

活动模型通常应来自 `agents.defaults.modelPreset`，并且该名称应指向 `modelPresets` 中的一个条目。直接使用 `agents.defaults.provider` 和 `agents.defaults.model` 对于旧配置仍然有效，但预设更易于切换，也更易于作为回退重用。

## 密钥设置

环境变量可将 API 密钥排除在配置文件之外。

使用你所选配方所示的变量名。下面的命令仅以 `OPENROUTER_API_KEY` 作为示例；OpenAI 直连配方使用 `OPENAI_API_KEY`，Anthropic 直连配方使用 `ANTHROPIC_API_KEY`，自定义端点可使用你在 `config.json` 中引用的任何变量名。

**macOS / Linux**

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
nanobot agent -m "Hello!"
```

**Windows PowerShell**

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
nanobot agent -m "Hello!"
```

以这种方式设置的环境变量仅适用于当前终端。对于 systemd、Docker、LaunchAgent 或远程 shell 等长期运行的服务，请在启动 nanobot 之前在该服务环境中设置变量。

## 配方：OpenRouter 网关

本配方适用于一个 API 密钥路由多个托管模型家族的情况。

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Primary",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

验证：

```bash
nanobot status
nanobot agent -m "Hello!"
```

如果失败并提示 `401` 或 `unauthorized`，请检查 `OPENROUTER_API_KEY` 在启动 nanobot 的同一终端或服务中是否可见。如果失败并提示 `model not found`，请选择 OpenRouter 为你的账户列出的模型 ID。

## 配方：OpenAI 直连

本配方适用于你拥有 OpenAI API 密钥并希望直接调用 OpenAI 而非通过网关调用的情况。

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "OpenAI",
      "provider": "openai",
      "model": "gpt-5",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

验证：

```bash
OPENAI_API_KEY="sk-..." nanobot agent -m "Hello!"
```

如果你的 shell 无法使用内联环境变量，请先设置 `OPENAI_API_KEY`，然后再运行 `nanobot agent -m "Hello!"`。如果 provider 拒绝 `apiType`，请移除 `apiType`，除非你正在使用有文档说明的 OpenAI 特定模式。

## 配方：Anthropic 直连

本配方适用于你的密钥来自 Anthropic、且模型名称是 Anthropic 模型 ID（而非 OpenRouter 模型路径）的情况。

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Anthropic",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

验证：

```bash
ANTHROPIC_API_KEY="sk-ant-..." nanobot agent -m "Hello!"
```

如果你复制了类似 `anthropic/claude-sonnet-4.5` 的模型名称，那是网关风格的模型路径，应放在 `provider: "openrouter"` 下，而不是 `provider: "anthropic"` 下。

如果你使用 Anthropic 兼容的代理，请将预设 provider 保持为 `anthropic`，并设置 `providers.anthropic.apiBase`：

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}",
      "apiBase": "https://anthropic-proxy.example.com"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Anthropic proxy",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

不要将 Anthropic 兼容端点配置为任意的自定义 provider 名称；命名的自定义 provider 使用 OpenAI 兼容的请求格式。

## 配方：自定义 OpenAI 兼容 Provider

本配方适用于不是已命名 nanobot provider 的 OpenAI 兼容服务。

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Custom",
      "provider": "custom",
      "model": "provider-model-name",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

在归咎于 nanobot 之前，先验证端点：

```bash
curl -sS https://api.example.com/v1/models
nanobot agent -m "Hello!"
```

`apiBase` 是 HTTP 基础 URL，不是模型名称。当服务需要时，包含版本路径，例如 `/v1`。如果服务要求非空密钥但不校验它，请使用占位符，例如 `"apiKey": "EMPTY"`。

对于多个自定义端点，不要让单个 `custom` 块过载。在 `providers` 下为每个端点命名，并从预设中引用同一名称：

```json
{
  "providers": {
    "workProxy": {
      "apiKey": "${WORK_PROXY_API_KEY}",
      "apiBase": "https://proxy.example.com/v1"
    },
    "lab-local": {
      "apiBase": "http://127.0.0.1:8000/v1"
    }
  },
  "modelPresets": {
    "work": {
      "label": "Work proxy",
      "provider": "workProxy",
      "model": "gpt-4o-mini",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "lab": {
      "label": "Lab local",
      "provider": "lab-local",
      "model": "served-model-name",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "work"
    }
  }
}
```

这些自定义名称的行为类似于直连 OpenAI 兼容 provider：需要 `apiBase`，当端点允许匿名或占位凭据时 `apiKey` 可选，且 `apiType` 应保持未设置。它们不支持 Anthropic 兼容端点；该情况请使用带 `apiBase` 的 `anthropic` provider。

## 配方：Ollama 本地模型

本配方适用于 Ollama 已安装且模型已在本地拉取的情况。

```bash
ollama serve
ollama pull llama3.2
```

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "local": {
      "label": "Local",
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local"
    }
  }
}
```

验证：

```bash
curl -sS http://localhost:11434/v1/models
nanobot agent -m "Hello!"
```

如果你看到 `connection refused`，说明 Ollama 未运行或 `apiBase` 指向了错误的端口。如果响应非常缓慢，请尝试更小的本地模型或更低的 `contextWindowTokens`。

## 配方：vLLM 或 LM Studio

本配方适用于本地服务器暴露 OpenAI 兼容的 `/v1` API 的情况。

```json
{
  "providers": {
    "vllm": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "modelPresets": {
    "local": {
      "label": "Local",
      "provider": "vllm",
      "model": "served-model-name",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local"
    }
  }
}
```

对于 LM Studio，使用其本地基础 URL 和 provider 名称：

```json
{
  "providers": {
    "lmStudio": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "local": {
      "label": "LM Studio",
      "provider": "lm_studio",
      "model": "local-model",
      "maxTokens": 2048,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local"
    }
  }
}
```

配置键可以是 `lmStudio` 或 `lm_studio`，但预设 provider 应使用注册名 `lm_studio`。

## 配方：回退预设

本配方适用于某个 provider 有时会限流、某个模型价格昂贵，或你想要本地备份的情况。

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    },
    "local": {
      "label": "Local",
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep", "local"]
    }
  }
}
```

`fallbackModels` 属于 `agents.defaults`。字符串条目是预设名称，不是原始模型名称。nanobot 会先尝试活动预设，然后按顺序尝试回退预设。

请保持回退候选方案的合理性。如果本地回退的上下文窗口较小，nanobot 必须构建能容纳活动链中最小窗口的上下文。

## 配方：Langfuse 追踪

本配方适用于 agent 已可用、且你希望对 OpenAI 兼容 provider 调用进行可观测的情况。

在运行 nanobot 的同一 Python 环境中安装可选包：

```bash
python -m pip install langfuse
```

在启动 nanobot 之前设置环境变量：

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
nanobot agent -m "Hello!"
```

PowerShell：

```powershell
$env:LANGFUSE_SECRET_KEY = "sk-lf-..."
$env:LANGFUSE_PUBLIC_KEY = "pk-lf-..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
nanobot agent -m "Hello!"
```

Langfuse 不是 `config.json` 中的模型 provider。它通过环境变量配置，并追踪支持的 OpenAI 兼容 provider 调用。不使用该客户端路径的原生 provider 可能不会产生 Langfuse OpenAI 包装器追踪。

## 配方：运行时切换模型

当你拥有多个预设并通过受支持的频道聊天时，使用此功能。

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536
    },
    "local": {
      "label": "Local",
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast"
    }
  }
}
```

在聊天中：

```text
/model
/model local
/model fast
```

`/model` 切换仅运行时有效。它不会重写 `config.json`，且进行中的对话回合会继续使用它开始时所用的模型。

## 快速故障对照表

| 症状 | 通常意味着 | 首先检查 |
|---|---|---|
| `401`、`unauthorized` 或 `invalid API key` | 密钥缺失、错误、过期，或在错误的 provider 下 | 在同一终端或服务中打印或重新设置环境变量 |
| `model not found` | 模型 ID 不属于所选 provider 或网关 | 对比 `modelPresets.<name>.provider` 和 `modelPresets.<name>.model` |
| `connection refused` | 本地服务器未运行，或 `apiBase` 端口/路径错误 | 运行 `curl <apiBase>/models` |
| `provider not found` | provider 名称拼写错误，或使用了配置键而非注册名 | 使用诸如 `openrouter`、`openai`、`anthropic`、`ollama`、`vllm`、`lm_studio` 等名称 |
| Langfuse 未显示追踪 | 环境变量缺失、`langfuse` 未安装在活动的 Python 环境中，或 provider 路径是原生的 | 运行 `python -m pip show langfuse` 并从同一环境重启 nanobot |

## 后续参考

| 需求 | 阅读 |
|---|---|
| 字段含义和 provider 解析 | [`providers.md`](./providers.md) |
| 完整 schema 和 provider 表 | [`configuration.md#providers`](./configuration.md#providers) |
| Langfuse 详情 | [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability) |
| 首次运行诊断 | [`troubleshooting.md`](./troubleshooting.md) |
