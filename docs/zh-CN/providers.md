# 提供商与模型

当首次回复因提供商/模型不匹配而失败时，或者当你想将具体的设置示例适配到其他提供商时，请使用本页。如果你已经知道想要哪个提供商，且只需要可直接粘贴的设置，请使用 [`provider-cookbook.md`](./provider-cookbook.md)。

对于每种设置，请回答三个问题：

1. 哪个提供商拥有该凭据或端点？
2. 该提供商期望的模型名称是什么？
3. 该提供商需要 `apiKey`、`apiBase`、OAuth 登录、云凭据，还是仅需本地服务器 URL？

优先为模型/提供商对使用命名的 `modelPresets` 条目，然后用 `agents.defaults.modelPreset` 选择它。直接使用 `agents.defaults.provider` 和 `agents.defaults.model` 对现有配置仍然有效，但预设能让运行时 `/model` 切换和回退链更清晰。在设置时请在预设内固定 `provider`；之后可以切换回 `"auto"`。

## 不靠猜测选择提供商

文档中显示具体的提供商名称是为了让 JSON 可直接复制，并非因为 nanobot 对提供商进行排名。从你实际控制的服务或端点出发：

| 如果你有... | 配置... |
|---|---|
| 来自托管提供商或网关的 API 密钥 | 该提供商的 `providers.<name>.apiKey`，然后创建一个包含该提供商名称和该服务模型 ID 的预设。 |
| 公司代理或区域端点 | 匹配的提供商块，如果代理提供了 URL，则加上 `apiBase`。 |
| 本地 OpenAI 兼容服务器 | 本地提供商块，例如 `ollama`、`vllm`、`lmStudio` 或 `custom`，通常需要 `apiBase`。 |
| 基于 OAuth 的账户 | 运行匹配的 `nanobot provider login ...` 命令，然后在预设中显式选择该提供商。 |
| 暂无提供商 | 在 nanobot 之外根据账户访问权限、定价、区域可用性、隐私要求以及所需的模型 ID 选择一个。然后带着其密钥和模型 ID 回来。 |

## 最简结构

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5",
      "maxTokens": 8192,
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

提供商配置为 nanobot 提供凭据和端点详情。模型预设命名了提供商/模型对。agent 默认设置选择在常规轮次中使用哪个命名预设。请同时替换示例中的提供商和模型；将一个提供商的 API 密钥与另一个提供商的模型 ID 混用是最常见的首次运行失败原因。

## 提供商、模型、API 密钥和 Base URL

这些字段回答不同的问题：

| 字段 | 所在位置 | 含义 |
|---|---|---|
| `provider` | `modelPresets.<name>.provider` | 应由哪个 nanobot 提供商适配器发送请求。 |
| `model` | `modelPresets.<name>.model` | 该提供商或网关期望的模型 ID。 |
| `apiKey` | `providers.<provider>.apiKey` | 该提供商的凭据。对敏感信息使用 `${ENV_VAR}`。 |
| `apiBase` | `providers.<provider>.apiBase` | 提供商端点的 HTTP base URL。 |

对于 OpenRouter、Anthropic 直连、OpenAI 直连、Groq 或 Bedrock 等托管内置提供商，你通常可以省略 `apiBase`，因为 nanobot 知道它们的默认端点。对于 `custom`、本地 OpenAI 兼容服务器、提供商代理、区域端点或订阅端点，请设置 `apiBase`。当端点需要时，请包含 API 版本路径，例如 `https://api.example.com/v1` 或 `http://localhost:11434/v1`。

## 常见提供商模式

### OpenRouter 网关

针对通过 OpenRouter 提供的模型 ID 的网关式设置。

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

请完全按照 OpenRouter 列出的方式使用模型 ID。

### Anthropic 直连

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Anthropic 直连使用原生 Anthropic 提供商。除非提供商是 OpenRouter，否则不要使用 OpenRouter 的模型 ID。

如果你使用 Anthropic 兼容的代理，请将提供商保持为 `anthropic` 并覆盖 `apiBase`：

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
      "provider": "anthropic",
      "model": "claude-sonnet-4-5"
    }
  }
}
```

任意自定义提供商名称仅兼容 OpenAI；它们不使用 Anthropic Messages API 请求格式。

### OpenAI 直连

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openai",
      "model": "gpt-5",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

当你需要强制使用特定的 OpenAI API 接口时，可以设置 `providers.openai.apiType`。其他提供商会拒绝 `apiType`；在 `providers.openai` 之外请保持未设置。请将模型替换为你的 OpenAI 账户可用的模型 ID。

### 自定义 OpenAI 兼容端点

`custom` 提供商适用于不由命名提供商表示的单个 OpenAI 兼容端点。

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "provider-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

`custom` 不会推断默认 base URL。请设置 `apiBase`。

如果你有多个自定义 OpenAI 兼容端点，请在 `providers` 下为每个端点提供各自的提供商键，并在模型预设中使用相同的键。该键可以是在你的环境中有意义的名称，例如 `companyProxy`、`tenant-a` 或 `dev-local`。

```json
{
  "providers": {
    "companyProxy": {
      "apiKey": "${COMPANY_PROXY_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    },
    "tenant-a": {
      "apiBase": "https://tenant-a.example.com/v1"
    }
  },
  "modelPresets": {
    "company": {
      "provider": "companyProxy",
      "model": "gpt-4o-mini",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    },
    "tenantA": {
      "provider": "tenant-a",
      "model": "served-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "company"
    }
  }
}
```

自定义提供商键被视为直连的 OpenAI 兼容提供商。`apiBase` 是必需的，因为 nanobot 无法知道端点 URL。对于不需要密钥的本地服务器或私有代理，`apiKey` 是可选的。请选择一个不与内置提供商名称或别名（例如 `openai`、`openai-codex`、`github-copilot` 或 `lm-studio`）冲突的名称。不要在自定义提供商键上设置 `apiType`；`apiType` 仅适用于 `providers.openai`。

这条命名自定义提供商路径不适用于 Anthropic 兼容端点。对于 Anthropic 兼容代理，请使用 `providers.anthropic.apiBase` 并将预设提供商设置为 `anthropic`。

### Ollama

单独启动 Ollama，然后将 nanobot 指向 OpenAI 兼容端点。

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

大多数 Ollama 设置不需要 API 密钥。

### vLLM 或其他本地 OpenAI 兼容服务器

```json
{
  "providers": {
    "vllm": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "vllm",
      "model": "served-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

某些 OpenAI 兼容的本地服务器要求任意非空的 API 密钥，即使它们并不验证它。

### LM Studio

```json
{
  "providers": {
    "lmStudio": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "lm_studio",
      "model": "local-model",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

配置键可以是 camelCase 或 snake_case。模型预设中的提供商名称应使用注册表名称，例如 `lm_studio`。

### AWS Bedrock

根据你的 AWS 设置，Bedrock 可以使用 AWS 凭据链、配置文件、区域或 Bedrock bearer token。

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "profile": "default"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "bedrock",
      "model": "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

有关 Bedrock 特定的说明，请参见 [`configuration.md#providers`](./configuration.md#providers)。

### OAuth 提供商

某些提供商不在 `config.json` 中使用 API 密钥。

```bash
nanobot provider login openai-codex
nanobot provider login github-copilot
```

然后在预设中显式选择提供商和模型。OAuth 提供商不是有效的自动回退选项。

## 提供商解析

推荐路径是由 `agents.defaults.modelPreset` 选择的命名预设。有效的模型参数来自：

1. 由 `agents.defaults.modelPreset` 引用的命名 `modelPresets` 条目；
2. 否则，由 `agents.defaults.model`、`provider`、`maxTokens`、`contextWindowTokens`、`temperature` 及相关字段构建的隐式 `default` 预设。

提供商选择遵循以下实用规则：

- 活动预设或隐式默认配置中显式的 `provider` 优先。
- `provider: "auto"` 会尝试模型名称关键字、已配置的密钥、本地 base URL 和网关提供商。
- 诸如 OpenRouter 和 AiHubMix 这样的网关提供商可以路由许多模型系列，因此模型名称必须对该网关有效。
- 本地提供商通常应显式指定，因为像 `llama3.2` 这样的通用本地模型名称并不总是包含提供商关键字。

### 模型名称前缀

`family/model-name` 并不总是选择提供商 `family`。基于前缀的提供商推断仅在活动提供商为 `"auto"` 时运行。

- 显式提供商优先：`provider: "openrouter"` 配合 `model: "anthropic/claude-sonnet-4.5"` 会调用 OpenRouter，而非 Anthropic。
- 当 `provider: "auto"` 时，匹配已配置的内置或命名自定义提供商的前缀可以选择该提供商。命名自定义前缀会在请求前被剥离，因此 `companyProxy/gpt-4o-mini` 会以 `gpt-4o-mini` 的形式发送到上游。
- 当使用显式命名自定义提供商时，模型按原样发送；`provider: "companyProxy"` 配合 `model: "openai/gpt-4o-mini"` 会将 `openai/gpt-4o-mini` 发送到 `companyProxy`。

当使用诸如 `anthropic/claude-sonnet-4.5` 这样的网关目录 ID 时，请在预设中固定 `provider`。

## 模型预设

模型预设是推荐的模型配置接口。当你需要命名的模型选择、运行时 `/model` 切换或可重用的回退目标时，请使用它们。

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
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast"
    }
  }
}
```

预设名称 `default` 保留给隐式的 `agents.defaults` 设置。不要定义 `modelPresets.default`；在较旧的配置中，使用 `/model default` 可返回直接使用 `agents.defaults.*` 字段。

## 回退模型

回退对于临时的提供商故障、速率限制或模型可用性问题很有用。请保持回退与任务规模和工具使用兼容。优先使用回退预设，这样每个候选都有一个名称以及完整的提供商、模型、生成和上下文窗口配置。

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
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    },
    "localSmall": {
      "label": "Local Small",
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 4096,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep", "localSmall"]
    }
  }
}
```

`fallbackModels` 中的字符串条目是预设名称，而非原始模型名称。nanobot 会在活动预设之后按顺序尝试它们。每个回退预设使用其自己的 `provider`、`model`、`maxTokens`、`contextWindowTokens`、`temperature` 以及可选的 `reasoningEffort`。

仅当某个模型不值得命名为预设时，才使用内联回退对象：

```json
{
  "modelPresets": {
    "fast": {
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": [
        {
          "provider": "deepseek",
          "model": "deepseek-v4-pro",
          "maxTokens": 4096,
          "contextWindowTokens": 262144
        }
      ]
    }
  }
}
```

`fallbackModels` 属于 `agents.defaults`，而非每个预设内部。如果回退候选使用较小的上下文窗口，nanobot 会使用活动链中最小的窗口来构建上下文，以便每个候选都能接收相同的提示。有关失败条件，请参见 [`configuration.md#model-fallbacks`](./configuration.md#model-fallbacks)。

## 快速检查

在调试聊天应用之前运行这些：

```bash
nanobot status
nanobot agent -m "Hello!"
```

如果 `nanobot agent -m "Hello!"` 失败：

| 症状 | 可能原因 |
|---|---|
| 401、未授权、无效 API 密钥 | 密钥缺失、过期、复制时带有空白字符，或存储在错误的提供商下 |
| 未找到模型 | 所选提供商或网关不存在该模型 ID |
| 连接被拒绝 | 本地提供商服务器未运行，或 `apiBase` 指向了错误的端口 |
| 未找到提供商 | 活动预设使用了拼写错误的提供商；请使用注册表名称，例如 `openrouter`、`anthropic`、`ollama`、`vllm`、`lm_studio` |
| 在 CLI 中可用但在聊天应用中不可用 | 提供商正常；请在 [`chat-apps.md`](./chat-apps.md) 或 [`troubleshooting.md`](./troubleshooting.md) 中调试网关/频道设置 |

有关完整的提供商表和高级的提供商特定说明，请参见 [`configuration.md#providers`](./configuration.md#providers)。
