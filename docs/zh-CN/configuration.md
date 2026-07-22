# 配置

配置文件：`~/.nanobot/config.json`

这是完整的参考文档。如果你是首次安装，请先阅读 [`quick-start.md`](./quick-start.md)。如果你正在尝试选择模型或修复 provider/model 匹配问题，请先使用 [`providers.md`](./providers.md)，然后再回到这里查看具体字段和高级选项。

下面的 JSON 示例通常是用于合并到现有配置中的部分片段，而不是完整的替换文件。关于配置、工作区、网关、频道、会话、工具和内存背后的心智模型，请参阅 [`concepts.md`](./concepts.md)。

生成的 `config.json` 使用 camelCase 键名，例如 `apiKey` 和 `intervalS`。为了兼容性，也接受 snake_case 键名，但文档优先使用 camelCase，因为这是 nanobot 写回磁盘的格式。

对于设置和运行时故障，请先按照 [`troubleshooting.md`](./troubleshooting.md) 中的诊断顺序进行排查，再同时更改多个配置区域。

> [!NOTE]
> 如果你的配置文件早于当前 schema，你可以在不覆盖现有值的情况下刷新它：运行 `nanobot onboard`，然后在询问是否覆盖配置时回答 `N`。nanobot 会合并缺失的默认字段并保留你当前的设置。

## 快速跳转

| 需求 | 章节 |
|---|---|
| 将密钥排除在 `config.json` 之外 | [用于密钥的环境变量](#environment-variables-for-secrets) |
| 追踪模型调用 | [Langfuse 可观测性](#langfuse-observability) |
| 配置凭证和端点 | [Providers](#providers) |
| 命名和切换模型选择 | [模型预设](#model-presets) |
| 添加回退链 | [模型回退](#model-fallbacks) |
| 配置语音转写 | [转写设置](#transcription-settings) |
| 调整频道默认值 | [频道设置](#channel-settings) |
| 配置网页搜索和抓取 | [Web 工具](#web-tools) |
| 启用图像生成 | [图像生成](#image-generation) |
| 添加 MCP 服务器 | [MCP](#mcp-model-context-protocol) |
| 审查 shell、工作区和 SSRF 控制 | [安全](#security) |
| 控制访问和配对 | [配对](#pairing) |
| 调整网关任务、会话和工具 | [网关心跳](#gateway-heartbeat)、[自动压缩](#auto-compact)、[统一会话](#unified-session)、[工具提示最大长度](#tool-hint-max-length) |

## 从哪里开始编辑

如果你不确定某个设置应该放在哪里，请从你要完成的任务开始。大多数更改只涉及一个配置章节和一个验证命令。

| 任务 | 首先检查的键 | 验证方式 | 深入了解 |
|---|---|---|---|
| 让第一次模型回复正常工作 | `providers.<name>.apiKey`，可选 `providers.<name>.apiBase`、`modelPresets.<preset>`、`agents.defaults.modelPreset` | `nanobot status`，然后 `nanobot agent -m "Hello!"` | [Providers](#providers)、[模型预设](#model-presets) |
| 添加回退模型 | `modelPresets.<fallback>`、`agents.defaults.fallbackModels` | `nanobot status`，然后正常运行 agent | [模型回退](#model-fallbacks) |
| 将密钥排除在配置文件之外 | 任何字符串值中的 `${ENV_VAR}` 占位符 | 从设置该变量的同一环境启动 nanobot | [用于密钥的环境变量](#environment-variables-for-secrets) |
| 打开内置 WebUI | `channels.websocket.enabled`，可选 `channels.websocket.port`、`channels.websocket.tokenIssueSecret` | `nanobot gateway`，然后打开 `http://127.0.0.1:8765` | [频道设置](#channel-settings)、[WebSocket 文档](./websocket.md) |
| 连接一个聊天应用 | `channels.<channel>.enabled`、频道凭证、`channels.<channel>.allowFrom` | `nanobot channels status`，然后 `nanobot gateway --verbose` | [频道设置](#channel-settings)、[聊天应用](./chat-apps.md) |
| 启用语音转写 | `transcription.enabled`、`transcription.provider`、匹配的 `providers.<name>.apiKey` | 通过已配置的界面发送或上传一段简短的语音消息 | [转写设置](#transcription-settings) |
| 启用网页搜索或抓取 | `tools.web.search.*`、`tools.web.fetch.*`，可选 `tools.ssrfWhitelist` | 提出一个需要当前网络信息的问题，然后如有需要检查日志 | [Web 工具](#web-tools)、[安全](#security) |
| 启用图像生成 | `tools.imageGeneration.enabled`、`tools.imageGeneration.provider`、`tools.imageGeneration.model`、匹配的 provider 凭证 | 在 WebUI 中启用图像生成并发送一个图像请求 | [图像生成](#image-generation) |
| 通过 MCP 添加外部工具 | `tools.mcpServers.<name>` | 启动 `nanobot gateway --verbose` 并检查启动/工具日志 | [MCP](#mcp-model-context-protocol) |
| 加强工具和网络安全 | `tools.restrictToWorkspace`、`tools.exec.sandbox`、`tools.ssrfWhitelist`、`channels.*.allowFrom` | 通过你计划暴露的频道或 CLI 运行相同的工作流 | [安全](#security)、[配对](#pairing) |
| 运行多个隔离的 bot | 分离的 `--config` 和 `--workspace` 路径，加上进程一起运行时不同的 `gateway.port` 或频道端口 | 以显式路径启动每个进程，仅对默认实例运行 `nanobot status` | [多实例](./multiple-instances.md)、[CLI 参考](./cli-reference.md) |
| 观察模型调用 | `LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_BASE_URL` 环境变量 | 运行一次模型调用，然后检查匹配的 Langfuse 项目 | [Langfuse 可观测性](#langfuse-observability) |

## 用于密钥的环境变量

你可以使用 `${VAR_NAME}` 引用（在启动时从环境变量解析），而不是将密钥直接存储在 `config.json` 中：

```json
{
  "channels": {
    "telegram": { "token": "${TELEGRAM_TOKEN}" },
    "email": {
      "imapPassword": "${IMAP_PASSWORD}",
      "smtpPassword": "${SMTP_PASSWORD}"
    }
  },
  "providers": {
    "groq": { "apiKey": "${GROQ_API_KEY}" }
  }
}
```

`config.json` 中的任何字符串值都可以使用 `${VAR_NAME}`。解析仅在启动时运行一次，且只在内存中进行——解析后的值永远不会写回磁盘，因此通过 `nanobot onboard` 或 WebUI 编辑配置会保留占位符。

如果引用的变量未设置，nanobot 在启动时会快速失败并报错 `ValueError: Environment variable 'NAME' referenced in config is not set`。

### 更多示例

**MCP 服务器** - 同时包括 stdio `env` 和 HTTP `headers`：

```json
{
  "tools": {
    "mcpServers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
      },
      "remote": {
        "url": "https://example.com/mcp/",
        "headers": { "Authorization": "Bearer ${REMOTE_MCP_TOKEN}" }
      }
    }
  }
}
```

**网页搜索 provider：**

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "${BRAVE_API_KEY}"
      }
    }
  }
}
```

### 在启动时加载变量

选择适合你部署的方式——nanobot 仅在启动时读取 `os.environ`，因此任何能填充进程环境变量的机制都有效。

**systemd** - 在服务单元中使用 `EnvironmentFile=` 从只有部署用户可读的文件中加载变量：

```ini
# /etc/systemd/system/nanobot.service（摘录）
[Service]
EnvironmentFile=/home/youruser/nanobot_secrets.env
User=nanobot
ExecStart=...
```

```bash
# /home/youruser/nanobot_secrets.env（权限 600，属于 youruser 所有）
TELEGRAM_TOKEN=your-token-here
IMAP_PASSWORD=your-password-here
```

**Docker** - 将环境变量文件传递给本地构建的镜像（每行一个 `KEY=VALUE`），或使用 `-e KEY=value`：

```bash
docker run --rm --env-file=./nanobot.env \
  -v ~/.nanobot:/home/nanobot/.nanobot \
  nanobot agent -m "Hello"
```

**direnv** - 在你的工作目录中放入一个 `.envrc` 并运行 `direnv allow`：

```bash
# .envrc（由 direnv 自动加载）
export TELEGRAM_TOKEN=your-token-here
export ANTHROPIC_API_KEY=...
```

**密钥管理器（1Password、Bitwarden、pass）** - 包装进程，使密钥仅在运行期间作为环境变量存在，绝不写入磁盘：

```bash
# 1Password - .env.tpl 中的引用形如 `op://Vault/Item/field`
op run --env-file=.env.tpl -- nanobot agent

# pass (passwordstore.org)
ANTHROPIC_API_KEY="$(pass show api/anthropic)" nanobot agent

# Bitwarden
ANTHROPIC_API_KEY="$(bw get password api/anthropic)" nanobot agent
```

## Langfuse 可观测性

nanobot 可以通过 Langfuse 的 OpenAI SDK 包装器追踪 OpenAI 兼容的 provider 调用。这通过环境变量配置，而不是 `config.json`。

在运行 nanobot 的同一 Python 环境中安装可选包：

```bash
python -m pip install langfuse
```

在启动 `nanobot agent`、`nanobot gateway` 或 `nanobot serve` 之前设置 Langfuse 凭证：

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

对于 PowerShell：

```powershell
$env:LANGFUSE_SECRET_KEY = "sk-lf-..."
$env:LANGFUSE_PUBLIC_KEY = "pk-lf-..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
```

当设置了 `LANGFUSE_SECRET_KEY` 且安装了 `langfuse` 包时，nanobot 对 OpenAI 兼容的 provider 使用 `langfuse.openai.AsyncOpenAI`，以便模型请求在后台发送到 Langfuse。如果设置了密钥但缺少 `langfuse`，nanobot 会记录警告并回退到常规的 OpenAI 客户端。

使用与你的项目匹配的 Langfuse 区域或自托管 URL。[Langfuse OpenAI SDK 文档](https://langfuse.com/integrations/model-providers/openai-py) 对云区域和自托管实例使用 `LANGFUSE_BASE_URL`。

追踪覆盖通过 nanobot 的 OpenAI 兼容客户端路径的 provider。不使用该客户端的原生 provider 可能不会产生 Langfuse OpenAI 包装器追踪。

## Providers

> [!TIP]
> - **语音转写**：语音消息和 WebUI 麦克风输入使用共享的顶层 `transcription` 设置。默认的 `transcription.provider` 值是 `"groq"`；设置为 `"openai"` 使用 OpenAI Whisper，`"openrouter"` 使用 OpenRouter 语音转文本模型，`"xiaomi_mimo"` 使用小米 MiMo ASR，或 `"assemblyai"` 使用 AssemblyAI。API 密钥仍然存放在匹配的 `providers.<provider>` 配置中。
> - **MiniMax Coding Plan**：nanobot 社区专属折扣链接：[海外](https://platform.minimax.io/subscribe/coding-plan?code=9txpdXw04g&source=link) · [中国大陆](https://platform.minimaxi.com/subscribe/token-plan?code=GILTJpMTqZ&source=link)
> - **MiniMax（中国大陆）**：如果你的 API 密钥来自 MiniMax 的中国大陆平台（minimaxi.com），请在你的 minimax provider 配置中设置 `"apiBase": "https://api.minimaxi.com/v1"`。
> - **MiniMax 思考模式**：`providers.minimaxAnthropic` 是用于 `reasoningEffort` / 思考模式的配置块。MiniMax 通过其 Anthropic 兼容端点暴露该能力，因此 nanobot 将其保留为单独的 provider，而不是在通用 OpenAI 兼容的 `minimax` 端点上猜测 MiniMax 特定的思考参数。它使用相同的 `MINIMAX_API_KEY`。默认的 Anthropic 兼容 base URL：`https://api.minimax.io/anthropic`；中国大陆请使用 `https://api.minimaxi.com/anthropic`。
> - **VolcEngine / BytePlus Coding Plan**：订阅端点通过专用 provider `volcengineCodingPlan` 或 `byteplusCodingPlan` 配置，与按量付费的 `volcengine` / `byteplus` provider 分开。
> - **Zhipu Coding Plan**：如果你使用的是 Zhipu 的 coding plan，请在你的 zhipu provider 配置中设置 `"apiBase": "https://open.bigmodel.cn/api/coding/paas/v4"`。
> - **阿里云百炼**：如果你使用阿里云百炼的 OpenAI 兼容端点，请在你的 dashscope provider 配置中设置 `"apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1"`。
> - **StepFun Step Plan**：如果你使用的是 StepFun 的 Step Plan 订阅，请在你的 stepfun provider 配置中设置 `"apiBase": "https://api.stepfun.ai/step_plan/v1"`。支持的模型包括 `step-3.5-flash`、`step-3.5-flash-2603` 和 `step-router-v1`。
> - **Step Fun（中国大陆）**：如果你的 API 密钥来自 Step Fun 的中国大陆平台（stepfun.com），请在你的 stepfun provider 配置中设置 `"apiBase": "https://api.stepfun.com/v1"`。
> - **小米 MiMo 思考模式**：MiMo 模型（例如 `mimo-v2.5-pro`）默认启用思考。使用 `agents.defaults.reasoningEffort: "none"` 禁用它，或使用 `"low"` / `"medium"` / `"high"` 保持启用。省略该字段会保留 provider 的每模型默认值。
> - **小米 MiMo Token Plan**：如果你使用的是 MiMo 的 token plan，请在你的 xiaomi_mimo provider 配置中设置 `"apiBase": "https://token-plan-sgp.xiaomimimo.com/v1"`。
> - **自定义 OpenAI 兼容 provider**：除了内置的 `custom` provider 外，`providers` 下的任何额外键都可以定义自己的 OpenAI 兼容端点。例如，`providers.companyProxy.apiBase` 加上 `modelPresets.primary.provider: "companyProxy"` 会创建一个单独的自定义 provider。设置 `apiBase`；仅当端点需要时才设置 `apiKey`。这个命名的自定义路径仅使用 OpenAI 兼容的请求格式。对于 Anthropic 兼容的代理，使用 `providers.anthropic.apiBase` 并设置 `provider: "anthropic"`。

| Provider | 用途 | 获取 API 密钥 |
|----------|---------|-------------|
| `custom` | 任何 OpenAI 兼容端点 | - |
| `openrouter` | LLM 网关，用于托管模型系列 + 语音转写（STT 模型） | [openrouter.ai](https://openrouter.ai) |
| `huggingface` | LLM（Hugging Face Inference Providers） | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| `skywork` | LLM（Skywork / APIFree API 网关） | [apifree.ai](https://www.apifree.ai) |
| `volcengine` | LLM（VolcEngine，按量付费） | [Coding Plan](https://www.volcengine.com/activity/codingplan?utm_campaign=nanobot&utm_content=nanobot&utm_medium=devrel&utm_source=OWO&utm_term=nanobot) · [volcengine.com](https://www.volcengine.com) |
| `volcengine_coding_plan` | LLM（VolcEngine Coding Plan 订阅端点） | [volcengine.com](https://www.volcengine.com/activity/codingplan?utm_campaign=nanobot&utm_content=nanobot&utm_medium=devrel&utm_source=OWO&utm_term=nanobot) |
| `byteplus` | LLM（VolcEngine 国际版，按量付费） | [Coding Plan](https://www.byteplus.com/en/activity/codingplan?utm_campaign=nanobot&utm_content=nanobot&utm_medium=devrel&utm_source=OWO&utm_term=nanobot) · [byteplus.com](https://www.byteplus.com) |
| `byteplus_coding_plan` | LLM（BytePlus Coding Plan 订阅端点） | [byteplus.com](https://www.byteplus.com/en/activity/codingplan?utm_campaign=nanobot&utm_content=nanobot&utm_medium=devrel&utm_source=OWO&utm_term=nanobot) |
| `anthropic` | LLM（Claude 直连） | [console.anthropic.com](https://console.anthropic.com) |
| `azure_openai` | LLM（Azure OpenAI） | [portal.azure.com](https://portal.azure.com) |
| `bedrock` | LLM（AWS Bedrock Converse，Claude/Nova/Llama 等） | [aws.amazon.com/bedrock](https://aws.amazon.com/bedrock/) |
| `openai` | LLM + 语音转写（Whisper） | [platform.openai.com](https://platform.openai.com) |
| `assemblyai` | 仅语音转写 | [assemblyai.com](https://www.assemblyai.com/) |
| `deepseek` | LLM（DeepSeek 直连） | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + 语音转写（Whisper，默认） | [console.groq.com](https://console.groq.com) |
| `minimax` | LLM（MiniMax 直连） | [platform.minimaxi.com](https://platform.minimaxi.com) |
| `minimax_anthropic` | LLM（MiniMax Anthropic 兼容端点，思考模式） | [platform.minimaxi.com](https://platform.minimaxi.com) |
| `gemini` | LLM（Gemini 直连） | [aistudio.google.com](https://aistudio.google.com) |
| `aihubmix` | LLM（API 网关，可访问所有模型） | [aihubmix.com](https://aihubmix.com) |
| `siliconflow` | LLM（SiliconFlow/硅基流动） | [siliconflow.cn](https://siliconflow.cn) |
| `novita` | LLM（Novita AI OpenAI 兼容网关） | [novita.ai](https://novita.ai) |
| `dashscope` | LLM（Qwen） | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM（Moonshot/Kimi） | [platform.kimi.com](https://platform.kimi.com?aff=nanobot) |
| `zhipu` | LLM（Zhipu GLM） | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `xiaomi_mimo` | LLM（MiMo） | [platform.xiaomimimo.com](https://platform.xiaomimimo.com) |
| `longcat` | LLM（LongCat） | [longcat.chat](https://longcat.chat/platform/docs/zh/) |
| `ant_ling` | LLM（Ant Ling / 蚂蚁百灵） | [developer.ant-ling.com](https://developer.ant-ling.com/en/docs/api-reference/openai/) |
| `ollama` | LLM（本地，Ollama） | - |
| `lm_studio` | LLM（本地，LM Studio） | - |
| `atomic_chat` | LLM（本地，[Atomic Chat](https://atomic.chat/)） | - |
| `mistral` | LLM | [docs.mistral.ai](https://docs.mistral.ai/) |
| `stepfun` | LLM（Step Fun/阶跃星辰）+ 语音转写（ASR） | [platform.stepfun.com](https://platform.stepfun.com) |
| `ovms` | LLM（本地，OpenVINO Model Server） | [docs.openvino.ai](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html) |
| `vllm` | LLM（本地，任何 OpenAI 兼容服务器） | - |
| `nvidia` | LLM（NVIDIA NIM） | [build.nvidia.com](https://build.nvidia.com/) |
| `openai_codex` | LLM（Codex，OAuth） | `nanobot provider login openai-codex` |
| `github_copilot` | LLM（GitHub Copilot，OAuth） | `nanobot provider login github-copilot` |
| `qianfan` | LLM（百度千帆） | [cloud.baidu.com](https://cloud.baidu.com/doc/qianfan/s/Hmh4suq26) |

<details>
<summary><b>OpenAI</b></summary>

默认情况下，OpenAI 使用 `apiType: "auto"`：nanobot 正常调用 Chat Completions，并在有用时将 GPT-5/o 系列或显式 `reasoningEffort` 请求通过 Responses API 路由。你可以强制使用特定的 API 表面：

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}",
      "apiType": "chat_completions"
    }
  }
}
```

有效的 `apiType` 值确切为 `auto`、`chat_completions` 和 `responses`。

`extraBody` 遵循所选的 OpenAI API 表面。使用 Chat Completions 时，nanobot 将其作为 SDK 的 `extra_body` 值传递。使用 Responses 时，按 Responses API body 形状配置；nanobot 将普通顶层字段合并到 Responses 请求体中，将 `extraBody.tools` 追加到生成的 function tools 之后，并合并且去重 `extraBody.include`：

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}",
      "apiType": "responses",
      "extraBody": {
        "tools": [{ "type": "web_search" }],
        "include": ["web_search_call.action.sources"]
      }
    }
  }
}
```

</details>

<details>
<summary><b>Azure OpenAI</b></summary>

`azure_openai` provider 通过 OpenAI **Responses API**（`/openai/v1/responses`）与你的 Azure OpenAI 资源通信。模型名称映射到**部署名称**，而不是 OpenAI 模型 ID。支持两种认证模式。

**模式 1：静态 API 密钥**（最简单）

```json
{
  "providers": {
    "azure_openai": {
      "apiKey": "${AZURE_OPENAI_API_KEY}",
      "apiBase": "https://my-resource.openai.azure.com"
    }
  },
  "modelPresets": {
    "azure": {
      "provider": "azure_openai",
      "model": "my-gpt-5-deployment"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "azure"
    }
  }
}
```

**模式 2：通过 `DefaultAzureCredential` 的 Microsoft Entra ID（Azure AD）**

省略 `apiKey`（或留空 / 不设置）。provider 回退到 [`DefaultAzureCredential`](https://learn.microsoft.com/azure/developer/python/sdk/authentication/credential-chains#defaultazurecredential-overview) 并为每个请求获取作用域为 `https://cognitiveservices.azure.com/.default` 的 bearer token。Azure SDK 自身的 MSAL 支持的缓存无需网络往返即可返回有效 token。

```json
{
  "providers": {
    "azure_openai": {
      "apiBase": "https://my-resource.openai.azure.com"
    }
  },
  "modelPresets": {
    "azure": {
      "provider": "azure_openai",
      "model": "my-gpt-5-deployment"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "azure"
    }
  }
}
```

安装可选依赖：

```bash
python -m pip install 'nanobot-ai[azure]'
```

`DefaultAzureCredential` 按以下顺序遍历此链并使用第一个成功的身份：

1. **EnvironmentCredential** - 读取 `AZURE_TENANT_ID`、`AZURE_CLIENT_ID` 以及 `AZURE_CLIENT_SECRET` / `AZURE_CLIENT_CERTIFICATE_PATH` / `AZURE_USERNAME` + `AZURE_PASSWORD` 之一。
2. **WorkloadIdentityCredential** - 用于 AKS 工作负载身份 / 联合令牌（`AZURE_FEDERATED_TOKEN_FILE`）。
3. **ManagedIdentityCredential** - 用于 Azure VM、App Service、Functions、Container Apps 等。
4. **AzureCliCredential** - 使用你开发机器上 `az login` 的 token。
5. **AzurePowerShellCredential** - 使用 `Connect-AzAccount` 的 token。
6. **AzureDeveloperCliCredential** - 使用 `azd auth login` 的 token。
7. **InteractiveBrowserCredential** *（默认禁用）*。

最终签署请求的身份**必须被分配 `Cognitive Services OpenAI User` RBAC 角色**（或更高角色）于 Azure OpenAI 资源上。没有该角色，你将在第一次请求时看到 `401`/`403` 错误。

> `apiBase` 在两种模式下都是必需的——它是你的 Azure 资源端点，无法推断。如果既没有设置 `apiKey` 也没有安装 `azure-identity`，provider 会抛出一个明确的错误，指向 `python -m pip install 'nanobot-ai[azure]'`。

</details>

<details>
<summary><b>Skywork / APIFree</b></summary>

Skywork 使用 APIFree 的 OpenAI 兼容 Agent API 端点。配置一次 provider，然后使用 Skywork 模型 ID，例如 `skywork-ai/skyclaw-v1`。

```json
{
  "providers": {
    "skywork": {
      "apiKey": "${SKYWORK_API_KEY}",
      "apiBase": "https://api.apifree.ai/agent/v1"
    }
  },
  "modelPresets": {
    "skywork": {
      "provider": "skywork",
      "model": "skywork-ai/skyclaw-v1",
      "maxTokens": 32768,
      "contextWindowTokens": 131072
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "skywork"
    }
  }
}
```

如果你的环境以 `${APIFREE_API_KEY}` 命名该凭证，你也可以在 `apiKey` 中引用它。

</details>

<details>
<summary><b>AWS Bedrock（Converse API）</b></summary>

Bedrock 使用原生 `bedrock-runtime` Converse API，因此它可以调用 Bedrock 模型 ID，例如 Claude Opus 4.7、Claude Sonnet、Amazon Nova、Meta Llama、Mistral、Qwen 以及其他支持 Converse 的模型。它支持普通聊天、流式传输、工具调用、工具结果、token 用量和 Bedrock 错误元数据。

此 provider 用于 Bedrock 的原生 Converse API，而不是 Bedrock 的 OpenAI 兼容 `/openai/v1` 端点。对于 OpenAI 兼容的 Bedrock 模型，如果你特别想要那个 API 表面，仍然可以使用 `custom`。

**1. 配置凭证**

使用正常的 AWS 凭证链（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`、AWS profile 或 IAM role）。IAM 身份需要：

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": "*"
}
```

你也可以将 `providers.bedrock.apiKey` 设置为 Bedrock API 密钥；nanobot 将其导出为 `AWS_BEARER_TOKEN_BEDROCK` 供 AWS SDK 使用。

凭证选项：

- **AWS CLI/默认 profile**：将 `apiKey` 和 `profile` 留空，然后运行 `aws configure` 或提供 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`。
- **命名的 AWS profile**：将 `profile` 设置为 `~/.aws/config` 或 `~/.aws/credentials` 中的 profile。
- **IAM role**：在 EC2/ECS/Lambda 上，将 `apiKey` 和 `profile` 留空并附加具有 Bedrock 权限的 role。
- **Bedrock API 密钥**：设置 `apiKey` 或 `AWS_BEARER_TOKEN_BEDROCK`；`profile` 可以保持 `null`。

**2. 最小配置**

对于非 Anthropic 模型，例如 Amazon Nova：

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1"
    }
  },
  "modelPresets": {
    "bedrockNova": {
      "provider": "bedrock",
      "model": "bedrock/amazon.nova-lite-v1:0",
      "reasoningEffort": null
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "bedrockNova"
    }
  }
}
```

使用 Bedrock API 密钥：

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "apiKey": "${AWS_BEARER_TOKEN_BEDROCK}"
    }
  },
  "modelPresets": {
    "bedrockNova": {
      "provider": "bedrock",
      "model": "bedrock/amazon.nova-lite-v1:0",
      "reasoningEffort": null
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "bedrockNova"
    }
  }
}
```

使用命名的 AWS profile：

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "profile": "my-bedrock-profile"
    }
  },
  "modelPresets": {
    "bedrockNova": {
      "provider": "bedrock",
      "model": "bedrock/amazon.nova-lite-v1:0"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "bedrockNova"
    }
  }
}
```

**3. Claude Opus 4.7 示例**

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1"
    }
  },
  "modelPresets": {
    "bedrockClaude": {
      "provider": "bedrock",
      "model": "bedrock/global.anthropic.claude-opus-4-7",
      "reasoningEffort": "medium",
      "maxTokens": 8192
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "bedrockClaude"
    }
  }
}
```

对于区域路由，使用 Bedrock 的推理 ID 之一，例如 `bedrock/us.anthropic.claude-opus-4-7`、`bedrock/eu.anthropic.claude-opus-4-7` 或 `bedrock/jp.anthropic.claude-opus-4-7`。

Claude Opus 4.7 不接受 `temperature`、`top_p` 或 `top_k`；nanobot 自动为此模型省略 `temperature`。如果 `reasoningEffort` 设置为 `low`、`medium`、`high`、`max` 或 `adaptive`，nanobot 会发送 Bedrock 的自适应思考参数。

Bedrock 上的 Anthropic 模型也可能需要 Anthropic 用例注册，并受 Anthropic 支持的国家/地区限制。如果 Claude 失败并报关于不支持的国家或地区的 `ValidationException`，请尝试非 Anthropic 的 Bedrock 模型（例如 Amazon Nova）来验证 provider 设置。

**4. 模型 ID**

在 nanobot 配置中使用带 `bedrock/` 前缀的 Bedrock 模型 ID 或推理 profile ID。nanobot 在调用 AWS 之前会移除该前缀。

示例：

- `bedrock/amazon.nova-micro-v1:0`
- `bedrock/amazon.nova-lite-v1:0`
- `bedrock/global.anthropic.claude-opus-4-7`
- `bedrock/us.anthropic.claude-opus-4-7`
- `bedrock/openai.gpt-oss-20b-1:0`
- `bedrock/meta.llama...`
- `bedrock/mistral...`

请查看 Bedrock 控制台以获取确切的模型 ID 和区域可用性。某些模型需要跨区域推理 profile ID，例如 `us.*`、`eu.*` 或 `global.*`。

**5. 高级模型字段**

模型特定的字段可以通过 `extraBody` 提供；nanobot 将其合并到 Converse 的 `additionalModelRequestFields` 中：

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "extraBody": {
        "thinking": {
          "type": "adaptive",
          "effort": "medium",
          "display": "summarized"
        }
      }
    }
  }
}
```

仅对自定义 Bedrock Runtime 端点 URL（例如 VPC 端点或代理）使用 `apiBase`。正常的 AWS 区域不需要它。

当前范围：nanobot 传递 `messages`、`system`、`inferenceConfig`、`toolConfig` 和 `additionalModelRequestFields`。Bedrock Prompt Management、Guardrails、`serviceTier` 和其他顶层 Converse 选项尚不是一等配置字段。

**6. 快速检查**

```bash
# 用于 AWS 凭证链用法：
aws sts get-caller-identity

# 用于 API 密钥用法：
export AWS_BEARER_TOKEN_BEDROCK="your-bedrock-api-key"
export AWS_REGION="us-east-1"
```

然后运行：

```bash
nanobot agent -m "Reply with one short sentence."
```

</details>


<details>
<summary><b>OpenAI Codex（OAuth）</b></summary>

Codex 使用 OAuth 而不是 API 密钥。需要 ChatGPT Plus 或 Pro 账户。`config.json` 中不需要 `providers.openaiCodex` 块；`nanobot provider login` 将 OAuth 会话存储在配置之外。

**1. 登录：**
```bash
nanobot provider login openai-codex
```

**2. 设置模型**（合并到 `~/.nanobot/config.json`）：
```json
{
  "modelPresets": {
    "codex": {
      "provider": "openai_codex",
      "model": "openai-codex/gpt-5.1-codex"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "codex"
    }
  }
}
```

**3. 聊天：**
```bash
nanobot agent -m "Hello!"

# 本地定位到特定工作区/配置
nanobot agent -c ~/.nanobot-telegram/config.json -m "Hello!"

# 在该配置之上的一次性工作区覆盖
nanobot agent -c ~/.nanobot-telegram/config.json -w /tmp/nanobot-telegram-test -m "Hello!"
```

> Docker 用户：使用 `docker run -it` 进行交互式 OAuth 登录。

</details>


<details>
<summary><b>GitHub Copilot（OAuth）</b></summary>

GitHub Copilot 使用 OAuth 而不是 API 密钥。需要配置了 [带有计划的 GitHub 账户](https://github.com/features/copilot/plans)。`config.json` 中不需要 `providers.githubCopilot` 块；`nanobot provider login` 将 OAuth 会话存储在配置之外。

**1. 登录：**
```bash
nanobot provider login github-copilot
```

**2. 设置模型**（合并到 `~/.nanobot/config.json`）：
```json
{
  "modelPresets": {
    "copilot": {
      "provider": "github_copilot",
      "model": "github-copilot/gpt-4.1"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "copilot"
    }
  }
}
```

**3. 聊天：**
```bash
nanobot agent -m "Hello!"

# 本地定位到特定工作区/配置
nanobot agent -c ~/.nanobot-telegram/config.json -m "Hello!"

# 在该配置之上的一次性工作区覆盖
nanobot agent -c ~/.nanobot-telegram/config.json -w /tmp/nanobot-telegram-test -m "Hello!"
```

> Docker 用户：使用 `docker run -it` 进行交互式 OAuth 登录。

</details>

<details>
<summary><b>LongCat（OpenAI 兼容）</b></summary>

LongCat 通过 nanobot 内置的 OpenAI 兼容 provider 流程提供。默认 API base 已指向 `https://api.longcat.chat/openai/v1`，因此你通常只需要设置 `apiKey`。

```json
{
  "providers": {
    "longcat": {
      "apiKey": "${LONGCAT_API_KEY}"
    }
  },
  "modelPresets": {
    "longcat": {
      "provider": "longcat",
      "model": "LongCat-2.0-Preview",
      "maxTokens": 8192,
      "contextWindowTokens": 1048576
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "longcat"
    }
  }
}
```

当前 LongCat API 文档将 `LongCat-2.0-Preview` 列为支持的模型。较旧的 `LongCat-Flash-*` 模型已于 2026-05-29 被 LongCat 停用。

</details>

<details>
<summary><b>小米 MiMo</b></summary>

当模型名称包含 `mimo` 时，`xiaomi_mimo` provider 会自动检测小米 MiMo 模型。默认 API base 是 `https://api.xiaomimimo.com/v1`。

> **Token Plan**：如果你使用的是 MiMo 的 token plan，请用专用端点覆盖 `apiBase`：
>
> ```json
> {
>   "providers": {
>     "xiaomi_mimo": {
>       "apiKey": "${XIAOMIMIMO_API_KEY}",
>       "apiBase": "https://token-plan-sgp.xiaomimimo.com/v1"
>     }
>   },
>   "modelPresets": {
>     "mimo": {
>       "provider": "xiaomi_mimo",
>       "model": "xiaomi/mimo-v2.5-pro"
>     }
>   },
>   "agents": {
>     "defaults": {
>       "modelPreset": "mimo"
>     }
>   }
> }
> ```
>
> 使用来自 MiMo token plan 控制台的模型 ID 和 API 密钥，并查看 MiMo 平台以获取最新支持的模型名称。

</details>

<details>
<summary><b>StepFun Step Plan（订阅）</b></summary>

Step Plan 是 StepFun 为高频 AI 开发者提供的基于订阅的服务。如果你使用的是 Step Plan 订阅，请在现有的 `stepfun` provider 配置中覆盖 `apiBase`，指向专用的 Step Plan 端点。

```json
{
  "providers": {
    "stepfun": {
      "apiKey": "${STEPFUN_API_KEY}",
      "apiBase": "https://api.stepfun.ai/step_plan/v1"
    }
  },
  "modelPresets": {
    "stepfun": {
      "provider": "stepfun",
      "model": "step-3.5-flash"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "stepfun"
    }
  }
}
```

支持的模型包括 `step-3.5-flash`、`step-3.5-flash-2603` 和 `step-router-v1`。

</details>

<details>
<summary><b>Ant Ling（OpenAI 兼容）</b></summary>

Ant Ling 通过 nanobot 内置的 OpenAI 兼容 provider 流程提供。默认 API base 指向 `https://api.ant-ling.com/v1`，因此你通常只需要设置 `apiKey`。

```json
{
  "providers": {
    "antLing": {
      "apiKey": "${ANT_LING_API_KEY}"
    }
  },
  "modelPresets": {
    "antLing": {
      "provider": "ant_ling",
      "model": "Ling-2.6-flash"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "antLing"
    }
  }
}
```

官方 OpenAI 兼容模型名称包括 `Ling-2.6-1T`、`Ling-2.6-flash`、`Ling-2.5-1T`、`Ling-1T`、`Ring-2.5-1T` 和 `Ring-1T`。

</details>

<details>
<summary><b>自定义 Provider（任何 OpenAI 兼容 API）</b></summary>

直接连接到任何 OpenAI 兼容端点——llama.cpp、Together AI、Fireworks、Azure OpenAI 或任何自托管服务器。模型名称按原样传递。

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1"
    }
  },
  "modelPresets": {
    "custom": {
      "provider": "custom",
      "model": "your-model-name"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "custom"
    }
  }
}
```

> 对于不需要认证的本地服务器，将 `apiKey` 设置为 `null`。
>
> `custom` 适用于暴露 OpenAI 兼容 **chat completions** API 的 provider。它**不会**将第三方端点强制推到 OpenAI/Azure **Responses API** 上。
>
> 如果你的代理或网关专门兼容 Responses API，请配置 `azure_openai` provider 形状并将 `apiBase` 指向该端点：
>
> ```json
> {
>   "providers": {
>     "azure_openai": {
>       "apiKey": "your-api-key",
>       "apiBase": "https://api.your-provider.com",
>       "defaultModel": "your-model-name"
>     }
>   },
>   "modelPresets": {
>     "responsesProxy": {
>       "provider": "azure_openai",
>       "model": "your-model-name"
>     }
>   },
>   "agents": {
>     "defaults": {
>       "modelPreset": "responsesProxy"
>     }
>   }
> }
> ```
>
> Anthropic 兼容端点是分开的：使用 `providers.anthropic.apiBase` 并将预设 provider 设置为 `anthropic`。任意自定义 provider 名称不使用 Anthropic Messages API 格式。
>
> 简而言之：**chat-completions 兼容端点 -> `custom` 或命名的自定义 provider**；**Responses 兼容端点 -> `azure_openai`**；**Anthropic 兼容端点 -> 带有 `apiBase` 的 `anthropic`**。

某些 OpenAI 兼容网关暴露请求体扩展，例如 vLLM 引导解码或本地采样控制。将它们放在 `extraBody` 下；nanobot 在其 provider 默认值之后将它们合并到 chat-completions 请求体中：

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1",
      "extraBody": {
        "repetition_penalty": 1.15,
        "chat_template_kwargs": {
          "enable_thinking": false
        }
      }
    }
  }
}
```

</details>

<a id="local-providers"></a>
<a id="ollama-local"></a>
<details>
<summary><b>Ollama（本地）</b></summary>

使用 Ollama 运行本地模型，然后添加到配置中：

**1. 启动 Ollama**（示例）：
```bash
ollama run llama3.2
```

**2. 添加到配置**（部分——合并到 `~/.nanobot/config.json`）：
```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434"
    }
  },
  "modelPresets": {
    "ollama": {
      "provider": "ollama",
      "model": "llama3.2"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "ollama"
    }
  }
}
```

> 当配置了 `providers.ollama.apiBase` 时，`provider: "auto"` 也可用，但在预设中固定 `"provider": "ollama"` 是最清晰的选项。

</details>

<details>
<summary><b>LM Studio（本地）</b></summary>

[LM Studio](https://lmstudio.ai/) 提供了一个本地 OpenAI 兼容服务器来运行 LLM。通过 LM Studio UI 下载模型，然后启动本地服务器。

**1. 启动 LM Studio 服务器：**
- 启动 LM Studio
- 转到"Local Server"标签
- 加载模型（例如 Llama、Mistral、Qwen）
- 点击"Start Server"（默认端口：1234）

**2. 添加到配置**（部分——合并到 `~/.nanobot/config.json`）：
```json
{
  "providers": {
    "lm_studio": {
      "apiKey": null,
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "lmStudio": {
      "provider": "lm_studio",
      "model": "local-model"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "lmStudio"
    }
  }
}
```

> **注意：** 将 `apiKey` 设置为 `null`，因为 LM Studio 在本地运行且不需要认证。模型名称应与 LM Studio UI 中显示的一致。当配置了 `providers.lm_studio.apiBase` 时，`provider: "auto"` 也可用，但在预设中固定 `"provider": "lm_studio"` 是最清晰的选项。

</details>

<a id="atomic-chat-local"></a>
<details>
<summary><b>Atomic Chat（本地）</b></summary>

[Atomic Chat](https://atomic.chat/) 是一个本地优先的桌面应用，暴露一个 **OpenAI 兼容**的 HTTP API（默认 `http://localhost:1337/v1`）。当你想在你自己的机器上运行 nanobot 对抗模型而不是托管 API provider 时，适用此设置。

**1. 启动 Atomic Chat**

- 在你的机器上安装 [Atomic Chat](https://atomic.chat/)。
- 打开 Atomic Chat，下载模型，并保持应用运行。本地 API 默认启用。
- 复制本地 API 暴露的模型 ID。例如，`Qwen 3 32B` 的模型 ID 可能是 `qwen3-32b`。

**2. 添加到配置**（部分——合并到 `~/.nanobot/config.json`）：

```json
{
  "providers": {
    "atomic_chat": {
      "apiKey": null,
      "apiBase": "http://localhost:1337/v1"
    }
  },
  "modelPresets": {
    "atomic": {
      "provider": "atomic_chat",
      "model": "qwen3-32b"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "atomic"
    }
  }
}
```

> **注意：** 用 Atomic Chat 中的模型 ID 替换 `qwen3-32b`。如果你的 Atomic Chat 服务器不需要密钥，请将 `apiKey` 设置为 `null`。如果需要，请将 `apiKey`（或 `ATOMIC_CHAT_API_KEY` 环境变量）设置为 Atomic Chat 期望的值。

> 当配置了 `providers.atomic_chat.apiBase` 时，`provider: "auto"` 也可用，但在预设中固定 `"provider": "atomic_chat"` 是最清晰的选项。

</details>

<details>
<summary><b>OpenVINO Model Server（本地 / OpenAI 兼容）</b></summary>

使用 [OpenVINO Model Server](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html) 在 Intel GPU 上本地运行 LLM。OVMS 在 `/v3` 暴露 OpenAI 兼容 API。

> 需要 Docker 和具有驱动访问权限（`/dev/dri`）的 Intel GPU。

**1. 拉取模型**（示例）：

```bash
mkdir -p ov/models && cd ov

docker run -d \
  --rm \
  --user $(id -u):$(id -g) \
  -v $(pwd)/models:/models \
  openvino/model_server:latest-gpu \
  --pull \
  --model_name openai/gpt-oss-20b \
  --model_repository_path /models \
  --source_model OpenVINO/gpt-oss-20b-int4-ov \
  --task text_generation \
  --tool_parser gptoss \
  --reasoning_parser gptoss \
  --enable_prefix_caching true \
  --target_device GPU
```

> 这会下载模型权重。等待容器完成后再继续。

**2. 启动服务器**（示例）：

```bash
docker run -d \
  --rm \
  --name ovms \
  --user $(id -u):$(id -g) \
  -p 8000:8000 \
  -v $(pwd)/models:/models \
  --device /dev/dri \
  --group-add=$(stat -c "%g" /dev/dri/render* | head -n 1) \
  openvino/model_server:latest-gpu \
  --rest_port 8000 \
  --model_name openai/gpt-oss-20b \
  --model_repository_path /models \
  --source_model OpenVINO/gpt-oss-20b-int4-ov \
  --task text_generation \
  --tool_parser gptoss \
  --reasoning_parser gptoss \
  --enable_prefix_caching true \
  --target_device GPU
```

**3. 添加到配置**（部分——合并到 `~/.nanobot/config.json`）：

```json
{
  "providers": {
    "ovms": {
      "apiBase": "http://localhost:8000/v3"
    }
  },
  "modelPresets": {
    "ovms": {
      "provider": "ovms",
      "model": "openai/gpt-oss-20b"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "ovms"
    }
  }
}
```

> OVMS 是本地服务器——不需要 API 密钥。支持工具调用（`--tool_parser gptoss`）、推理（`--reasoning_parser gptoss`）和流式传输。有关更多详细信息，请参阅 [官方 OVMS 文档](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html)。
</details>

<a id="vllm-local-openai-compatible"></a>
<details>
<summary><b>vLLM（本地 / OpenAI 兼容）</b></summary>

使用 vLLM 或任何 OpenAI 兼容服务器运行你自己的模型，然后添加到配置中：

**1. 启动服务器**（示例）：
```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

**2. 添加到配置**（部分——合并到 `~/.nanobot/config.json`）：

*Provider（本地服务器将 API 密钥设置为 null）：*
```json
{
  "providers": {
    "vllm": {
      "apiKey": null,
      "apiBase": "http://localhost:8000/v1"
    }
  }
}
```

*模型预设：*
```json
{
  "modelPresets": {
    "vllm": {
      "provider": "vllm",
      "model": "meta-llama/Llama-3.1-8B-Instruct"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "vllm"
    }
  }
}
```

</details>

添加新 provider 的贡献者说明位于 [`development.md`](./development.md#adding-an-llm-provider)。

## 模型预设

模型预设让你命名一个完整的模型配置，并在运行时使用 `/model <preset>` 切换。它们是配置模型的推荐方式，因为相同的名称可以重用于启动选择、聊天命令切换和回退链。

现有配置不需要更改。直接的 `agents.defaults.model`、`provider`、`maxTokens`、`contextWindowTokens`、`temperature` 和 `reasoningEffort` 字段仍然定义隐式的 `default` 预设。对于新配置，优先使用顶层 `modelPresets` 加上 `agents.defaults.modelPreset`。

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
      "fallbackModels": ["deep", "localSmall"]
    }
  },
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "model": "gpt-4.1-mini",
      "provider": "openai",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.2,
      "reasoningEffort": "low"
    },
    "deep": {
      "label": "Deep",
      "model": "claude-opus-4-5",
      "provider": "anthropic",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "reasoningEffort": "high"
    },
    "localSmall": {
      "label": "Local Small",
      "model": "llama3.2",
      "provider": "ollama",
      "maxTokens": 4096,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  }
}
```

`modelPresets` 是一个顶层对象。其下的键（`fast`、`deep`、`coding` 等）是用户定义的预设名称。每个预设支持：

| 字段 | 描述 |
|-------|-------------|
| `label` | 在模型列表中显示的可选名称。 |
| `model` | 用于此预设的模型名称。 |
| `provider` | provider 名称，或 `"auto"` 使用 provider 自动检测。 |
| `maxTokens` | 最大补全/输出 token。 |
| `contextWindowTokens` | 提示构建和整合决策使用的上下文窗口大小。 |
| `temperature` | 采样温度。 |
| `reasoningEffort` | 可选的推理/思考设置。Provider 支持情况各异。 |

`default` 是保留的，始终表示由直接 `agents.defaults.*` 字段构建的隐式预设；不要定义 `modelPresets.default`。使用 `/model default` 在现有配置中切换回那些直接字段。

设置 `agents.defaults.modelPreset` 来选择启动预设。当 `modelPreset` 为 `null` 或省略时，启动使用来自直接 `agents.defaults.*` 字段的隐式 `default` 预设。使用 `/model <preset>` 进行的运行时更改不会写回 `config.json`；它们影响未来的轮次，直到进程重启或另一个模型/配置更改替换它们。

### 模型回退

`agents.defaults.fallbackModels` 为活动模型配置定义一个有序的故障转移链。主模型仍由 `agents.defaults.modelPreset` 选择，或在较旧的配置中由直接 `agents.defaults.*` 字段的隐式 `default` 预设选择。

每个回退候选可以是：

- 来自 `modelPresets` 的预设名称，例如 `"deep"`。这是推荐的形式。使用预设的完整模型、provider、生成和上下文窗口配置。
- 一个内联回退对象，至少包含 `provider` 和 `model`。可选的 `maxTokens`、`contextWindowTokens` 和 `temperature` 字段在省略时从活动主配置继承。`reasoningEffort` 不继承；省略它以对该回退关闭推理，或为支持推理的模型显式设置它。

预设回退链：

```json
{
  "modelPresets": {
    "fast": {
      "model": "gpt-4.1-mini",
      "provider": "openai",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.2
    },
    "deep": {
      "model": "claude-opus-4-5",
      "provider": "anthropic",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "reasoningEffort": "high"
    },
    "localSmall": {
      "model": "llama3.2",
      "provider": "ollama",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
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

字符串条目是预设名称，而不是原始模型名称。在上面的示例中，`"deep"` 表示 `modelPresets.deep`；nanobot 不会将其解释为 provider 模型 ID。更改预设会同时更新 `/model <preset>` 切换和引用它的任何回退链。

内联回退对象：

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

仅当回退不值得命名为可重用的预设时才使用内联对象。`fallbackModels` 属于 `agents.defaults`，而不是单个 `modelPresets` 条目内部。

故障转移通常在主 provider 返回可重试的模型/provider 错误且尚未流式传输任何回答文本之前运行。流式停顿超时是恢复的例外：如果 provider 已经发出了部分回答文本然后停顿，nanobot 会关闭当前流段并在新段中重试/故障转移。典型的回退情况包括超时、连接错误、5xx 服务器错误、429 速率限制、过载和配额/余额耗尽。它不会针对格式错误的请求、认证/权限错误、内容过滤/拒绝或上下文长度/消息格式错误运行。

如果回退候选使用较小的 `contextWindowTokens` 值，nanobot 会使用活动链中最小的窗口构建上下文，以便每个候选都能接收相同的提示。

## 转写设置

音频转写是聊天频道语音消息和 WebUI 麦克风输入使用的共享能力。聊天频道语音消息在进入 agent 之前会自动转写。WebUI 麦克风输入首先转写到编辑器中，以便你可以在发送前编辑文本。

在顶层 `transcription` 部分配置转写：

```json
{
  "transcription": {
    "enabled": true,
    "provider": "groq",
    "model": null,
    "language": null,
    "maxDurationSec": 120,
    "maxUploadMb": 25
  }
}
```

| 设置 | 默认值 | 描述 |
|---------|---------|-------------|
| `enabled` | `true` | 为聊天频道语音消息和 WebUI 麦克风输入启用音频转写。 |
| `provider` | `"groq"` | 转写后端：`"groq"`、`"openai"`、`"openrouter"`、`"xiaomi_mimo"`、`"stepfun"` 或 `"assemblyai"`。 |
| `model` | provider 默认值 | 可选的转写模型覆盖。默认为 Groq 使用 `whisper-large-v3`，OpenAI 使用 `whisper-1`，OpenRouter 使用 `openai/whisper-1`，小米 MiMo ASR 使用 `mimo-v2.5-asr`，StepFun ASR 使用 `stepaudio-2.5-asr`，AssemblyAI 使用 `universal-3-pro,universal-2`。OpenRouter 在其转写端点上仅接受语音转文本模型，例如 `nvidia/parakeet-tdt-0.6b-v3`、`openai/whisper-1` 或 `openai/gpt-4o-transcribe`；聊天 LLM 在那里会被拒绝。AssemblyAI 接受逗号分隔的模型回退列表。 |
| `language` | `null` | 可选的 ISO-639 语言提示，例如 `"en"`、`"zh"`、`"ko"` 或 `"ja"`。 |
| `maxDurationSec` | `120` | 最大 WebUI 录音时长。 |
| `maxUploadMb` | `25` | 最大 WebUI 音频上传大小。 |

provider 和语言解析的顺序是为向后兼容而有意设计的：

1. `transcription.provider` / `transcription.language`
2. 旧版 `channels.transcriptionProvider` / `channels.transcriptionLanguage`
3. 内置默认值（`provider: "groq"`，无语言提示）

旧版 `channels.*` 转写字段在转写成为聊天频道和 WebUI 麦克风输入的共享能力之前就存在了。仍然读取它们以便较旧的 `config.json` 文件继续工作，但它们不再是首选的配置表面。如果新旧字段都存在，顶层 `transcription` 值是权威来源。

转写凭证有意不存储在 `transcription` 中。将 API 密钥和可选端点放在匹配的 provider 配置中：

```json
{
  "providers": {
    "groq": {
      "apiKey": "gsk-...",
      "apiBase": "https://api.groq.com/openai/v1"
    }
  },
  "transcription": {
    "provider": "groq",
    "language": "zh"
  }
}
```

选择转写 provider 本身不会配置凭证。例如，为了兼容性，有效的 provider 可能默认为 Groq，但只有当 `providers.groq.apiKey` 或匹配的环境变量支持配置可用时，转写才可用。设置 UI 仅写入顶层 `transcription` 字段。

如果你要添加新的转写 provider，请参阅 [`development.md`](./development.md#adding-a-transcription-provider)。

## 频道设置

适用于所有频道的全局设置。在 `~/.nanobot/config.json` 中的 `channels` 部分下配置：

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "extractDocumentText": true,
    "sendMaxRetries": 3,
    "telegram": {
      "enabled": false
    }
  }
}
```

| 设置 | 默认值 | 描述 |
|---------|---------|-------------|
| `sendProgress` | `true` | 将 agent 的文本进度流式传输到频道 |
| `sendToolHints` | `false` | 流式传输工具调用提示（例如 `read_file("…")`） |
| `showReasoning` | `true` | 允许频道呈现模型推理/思考内容（DeepSeek-R1 `reasoning_content`、Anthropic `thinking_blocks`、内联 `<think>` 标签）。推理作为带有 `_reasoning_delta` / `_reasoning_end` 标记的专用流流动——频道重写 `send_reasoning_delta` / `send_reasoning_end` 以渲染就地更新。即使为 `true`，没有这些重写的频道会静默保持无操作。目前在 CLI 和 WebSocket/WebUI 上呈现（斜体微光标题，流结束后自动折叠）；Telegram / Slack / Discord / 飞书 / 微信 / Matrix 保持基础无操作，直到它们的气泡 UI 适配。独立于 `sendProgress`。 |
| `extractDocumentText` | `true` | 将支持的文档/文本附件提取到模型提示中。设置为 `false` 可将文档内容排除在提示之外，改为包含附件路径引用。 |
| `sendMaxRetries` | `3` | 每条出站消息的最大投递尝试次数，包括初始发送（配置 0-10，实际最少 1 次尝试） |

`channels.transcriptionProvider` 和 `channels.transcriptionLanguage` 是已弃用的兼容字段。它们作为较旧配置的只读回退保留，但新配置应使用顶层 `transcription.provider` 和 `transcription.language`。

`sendProgress` 和 `sendToolHints` 也可以按频道覆盖。全局值作为未设置自己值的频道的默认值：

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "telegram": {
      "enabled": true,
      "sendProgress": false
    },
    "websocket": {
      "enabled": true,
      "sendToolHints": true
    }
  }
}
```

### 重试行为

重试是有意简化的。

当频道 `send()` 抛出异常时，nanobot 在频道管理器层重试。默认情况下，`channels.sendMaxRetries` 为 `3`，该计数包括初始发送。

- **尝试 1**：立即发送
- **尝试 2**：`1s` 后重试
- **尝试 3**：`2s` 后重试
- **更高的重试预算**：退避继续为 `1s`、`2s`、`4s`，然后保持上限为 `4s`
- **瞬时故障**：网络抖动和临时 API 限制通常在下一次尝试时恢复
- **永久故障**：无效的 token、被撤销的访问或被封禁的频道会耗尽重试预算并干净地失败

> [!NOTE]
> 这个设计是有意的：频道实现应该在投递失败时抛出异常，频道管理器拥有共享的重试策略。
>
> 某些频道可能仍会在内部应用小的 API 特定重试。例如，Telegram 在向管理器呈现最终失败之前，会单独重试超时和洪水控制错误。
>
> 如果频道完全不可达，nanobot 无法通过同一频道通知用户。查看日志中的 `Failed to send to {channel} after N attempts` 以发现持续的投递失败。

## Web 工具

nanobot 包含访问网络的基本工具。这些包括通过 API 搜索，以及以 Markdown 格式抓取任意网页。它们默认启用，可以在 `~/.nanobot/config.json` 中的 `tools.web` 下配置。

如果你想禁用它们（从发送给 LLM 的工具列表中移除 `web_search` 和 `web_fetch`），请将 `tools.web.enable` 设置为 `false`：

```json
{
  "tools": {
    "web": {
      "enable": false
    }
  }
}
```

nanobot 对内置网页抓取和 HTTP/SSE MCP 连接使用共享的 SSRF 防护。默认情况下，它阻止环回地址、RFC1918/私有范围、CGNAT/Tailscale 范围、链路本地地址和云元数据端点。如果你需要允许受信任的私有范围，请使用 `tools.ssrfWhitelist` 将它们显式排除在 SSRF 阻止之外：

```json
{
  "tools": {
    "ssrfWhitelist": ["100.64.0.0/10"]
  }
}
```

保持白名单条目尽可能窄，例如单个主机 CIDR（`192.168.1.50/32`）。白名单对于共享 SSRF 防护是全局的；它不限于一个工具或一个 MCP 服务器。

> [!TIP]
> 使用 `tools.web` 中的 `proxy` 通过代理路由所有网页请求（搜索 + 抓取）：
> ```json
> { "tools": { "web": { "proxy": "http://127.0.0.1:7890" } } }
> ```

### `tools.web`

| 选项 | 类型 | 默认值 | 描述 |
|--------|------|---------|-------------|
| `enable` | boolean | `true` | 启用或禁用所有内置网页工具（`web_search` + `web_fetch`） |
| `proxy` | string 或 null | `null` | 所有网页请求的代理，例如 `http://127.0.0.1:7890` |
| `userAgent` | string 或 null | `null` | 所有网页请求的 User-Agent 头。如果为 null，将使用浏览器 UA |

### 网页搜索

nanobot 支持多个网页搜索 provider。在 `~/.nanobot/config.json` 中的 `tools.web.search` 下配置。

默认情况下，网页搜索使用 `duckduckgo`，它开箱即用，无需 API 密钥。

| Provider | 配置字段 | 环境变量回退 | 免费 |
|----------|--------------|------------------|------|
| `brave` | `apiKey` | `BRAVE_API_KEY` | 否 |
| `tavily` | `apiKey` | `TAVILY_API_KEY` | 否 |
| `jina` | `apiKey` | `JINA_API_KEY` | 免费层（10M token） |
| `kagi` | `apiKey` | `KAGI_API_KEY` | 否 |
| `olostep` | `apiKey` | `OLOSTEP_API_KEY` | 否 |
| `bocha` | `apiKey` | `BOCHA_API_KEY` | 免费层（初创公司 1M 次调用） |
| `volcengine` | `apiKey` | `VOLCENGINE_SEARCH_API_KEY` 或 `WEB_SEARCH_API_KEY` | 月度配额，之后付费 |
| `keenable` | `apiKey`（可选） | `KEENABLE_API_KEY` | 是（无需密钥；密钥提高限制） |
| `searxng` | `baseUrl` | `SEARXNG_BASE_URL` | 是（自托管） |
| `duckduckgo`（默认） | - | - | 是 |

**Brave：**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "${BRAVE_API_KEY}"
      }
    }
  }
}
```

**Tavily：**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "tavily",
        "apiKey": "${TAVILY_API_KEY}"
      }
    }
  }
}
```

**Jina**（免费层，10M token）：
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "jina",
        "apiKey": "${JINA_API_KEY}"
      }
    }
  }
}
```

**Kagi：**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "kagi",
        "apiKey": "${KAGI_API_KEY}"
      }
    }
  }
}
```

**Olostep：**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "olostep",
        "apiKey": "${OLOSTEP_API_KEY}"
      }
    }
  }
}
```

你也可以在环境中设置 `OLOSTEP_API_KEY` 而不是将其存储在配置中。

**Bocha**（AI 优化搜索，提供免费层）：
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "bocha",
        "apiKey": "${BOCHA_API_KEY}"
      }
    }
  }
}
```

在 [open.bochaai.com](https://open.bochaai.com) 创建你的 API 密钥。
Bocha 返回为 AI 消费优化的结构化结果，带有可选摘要。
你可以在环境中设置 `BOCHA_API_KEY` 而不是将其存储在配置中。

**Volcengine Search：**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "volcengine",
        "apiKey": "${VOLCENGINE_SEARCH_API_KEY}"
      }
    }
  }
}
```

你也可以设置 `WEB_SEARCH_API_KEY` 以兼容 Volcengine 网页搜索技能。在 [Volcengine 网页搜索控制台](https://console.volcengine.com/search-infinity/web-search) 中创建密钥，然后从 [API 密钥](https://console.volcengine.com/search-infinity/api-key) 复制。Volcengine Ark 密钥是分开的，不适用于此搜索 provider。

**Keenable**（在免费层无需 API 密钥即可工作）：
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "keenable"
      }
    }
  }
}
```

Keenable 搜索通过其无 token 的公共端点开箱即用（免费层，限制为每小时 1,000 次请求）。从 [keenable.ai](https://keenable.ai) 设置 `apiKey`（或 `KEENABLE_API_KEY`）以移除小时限制。

**SearXNG**（自托管，无需 API 密钥）：
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "searxng",
        "baseUrl": "https://searx.example"
      }
    }
  }
}
```

**DuckDuckGo**（零配置）：
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```

#### `tools.web.search`

| 选项 | 类型 | 默认值 | 描述 |
|--------|------|---------|-------------|
| `provider` | string | `"duckduckgo"` | 搜索后端：`brave`、`tavily`、`jina`、`kagi`、`olostep`、`bocha`、`volcengine`、`keenable`、`searxng`、`duckduckgo` |
| `apiKey` | string | `""` | 用于 API 支持的搜索 provider 的 API 密钥 |
| `baseUrl` | string | `""` | SearXNG 的 base URL |
| `maxResults` | integer | `5` | 每次搜索的结果数（1–10） |

### 网页抓取

> [!TIP]
> 如果你遇到 JS 工作证明或 Cloudflare 验证码问题，请设置随机 user agent 并禁用 Jina Reader：
> ```json
> { "tools": { "web": { "userAgent": "Not-A-Browser", "fetch": { "useJinaReader": false } } } }
> ```

nanobot 默认使用 [Jina Reader](https://jina.ai/reader/)（一个第三方 API）将任意页面转换为 Markdown 格式以便 LLM 轻松消化，如果前者失败，则使用基于 [readability-lxml](https://github.com/buriy/python-readability) 的本地回退。

如果你想始终使用本地转换，可以使用以下配置强制：

```json
{
  "tools": {
    "web": {
      "fetch": {
        "useJinaReader": false
      }
    }
  }
}
```

#### `tools.web.fetch`

| 选项 | 类型 | 默认值 | 描述 |
|--------|------|---------|-------------|
| `useJinaReader` | boolean | `true` | 如果为 true，将优先使用 Jina Reader 而不是本地转换 |

## 图像生成

图像生成在 `tools.imageGeneration` 下配置，并使用所选 provider 的 `providers.<name>` 块中的凭证。

有关 WebUI 用法、provider 示例、产物存储和故障排除，请参阅 [图像生成](./image-generation.md)。

## MCP（Model Context Protocol）

> [!TIP]
> 配置格式与 Claude Desktop / Cursor 兼容。你可以直接从任何 MCP 服务器的 README 复制 MCP 服务器配置。

nanobot 支持 [MCP](https://modelcontextprotocol.io/)——连接外部工具服务器并将它们用作原生 agent 工具。

将 MCP 服务器添加到你的 `config.json`：

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "my-remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxxxx"
        }
      }
    }
  }
}
```

支持两种传输模式：

| 模式 | 配置 | 示例 |
|------|--------|---------|
| **Stdio** | `command` + `args` | 通过 `npx` / `uvx` 的本地进程 |
| **HTTP** | `url` + `headers`（可选） | 远程端点（`https://mcp.example.com/sse`） |

> [!IMPORTANT]
> HTTP/SSE MCP URL 在探测或连接之前会被验证，每个传出的 MCP HTTP 请求在跟随重定向之前会再次验证。`localhost`、`127.0.0.1`、RFC1918/私有 IP、CGNAT/Tailscale 范围、链路本地地址和云元数据端点默认被阻止。这可能会破坏以前可用的本地或私有 HTTP MCP 配置，直到使用 `tools.ssrfWhitelist` 显式允许该端点，最好使用单个主机 CIDR，例如 `127.0.0.1/32`、`::1/128` 或 `192.168.1.50/32`。Stdio MCP 服务器不受影响。

使用 `toolTimeout` 覆盖慢速服务器的默认 30s 每次调用超时：

```json
{
  "tools": {
    "mcpServers": {
      "my-slow-server": {
        "url": "https://example.com/mcp/",
        "toolTimeout": 120
      }
    }
  }
}
```

使用 `enabledTools` 仅注册 MCP 服务器的工具子集：

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file", "mcp_filesystem_write_file"]
      }
    }
  }
}
```

`enabledTools` 接受原始 MCP 工具名称（例如 `read_file`）或包装的 nanobot 工具名称（例如 `mcp_filesystem_write_file`）。

- 省略 `enabledTools`，或设置为 `["*"]`，以注册所有工具。
- 将 `enabledTools` 设置为 `[]` 以不注册该服务器的任何工具。
- 将 `enabledTools` 设置为非空的名称列表以仅注册该子集。

MCP 工具在启动时自动发现并注册。LLM 可以将它们与内置工具一起使用——无需额外配置。




## 安全

> [!TIP]
> 对于生产部署，请在配置中同时设置 `"restrictToWorkspace": true` 和 `"tools.exec.sandbox": "bwrap"`。`restrictToWorkspace` 启用 nanobot 的应用级工作区防护；`tools.exec.sandbox` 为 shell 命令提供进程级隔离。

对于 API 密钥、token 和其他密钥，请参阅[用于密钥的环境变量](#environment-variables-for-secrets)——避免将它们直接存储在 `config.json` 中。

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `false` | 当为 `true` 时，为工作区感知工具启用 nanobot 的应用级工作区防护。文件工具在活动工作区下解析路径；选定的内部根目录可以添加为只读或显式可写根目录，媒体上传默认为只读。Shell 执行拒绝工作区外部的 `working_dir` 值并应用尽力而为的命令路径检查，但这不是 OS 沙箱。 |
| `tools.exec.sandbox` | `""` | shell 命令的沙箱后端。设置为 `"bwrap"` 可将 exec 调用包装在 [bubblewrap](https://github.com/containers/bubblewrap) 沙箱中——进程只能看到工作区（读写）和媒体目录（只读）；配置文件和 API 密钥被隐藏。自动为文件工具启用工作区限制。**仅限 Linux**——需要安装 `bwrap`（`apt install bubblewrap`；Docker 镜像中预装）。在 macOS 或 Windows 上不可用（bwrap 依赖 Linux 内核命名空间）。 |
| `tools.exec.enable` | `true` | 当为 `false` 时，shell `exec` 工具完全不注册。使用此选项可完全禁用 shell 命令执行。 |
| `tools.exec.timeout` | `60` | shell 命令的默认硬超时（秒）。配置值可以超过每次调用工具上限；设置为 `0` 可为受信任的长时间运行命令禁用硬超时。 |
| `tools.exec.pathPrepend` | `""` | 运行 shell 命令时预追加到 `PATH` 的额外目录。当配置的工具应优先于可执行文件查找时使用，例如 Python 虚拟环境的 `bin` 或 `Scripts` 目录。 |
| `tools.exec.pathAppend` | `""` | 运行 shell 命令时追加到 `PATH` 的额外目录（例如 `/usr/sbin` 用于 `ufw`）。 |
| `tools.ssrfWhitelist` | `[]` | 从网页抓取和 HTTP/SSE MCP 连接使用的共享 SSRF 防护中排除的 CIDR 范围。优先使用精确的主机 CIDR，例如 `192.168.1.50/32`；广泛范围会增加 SSRF 暴露。 |
| `channels.*.allowFrom` | 省略 | 每个频道的访问控制。省略以使用仅配对模式；设置 `["*"]` 允许所有人；或列出特定用户 ID。详情请参阅[配对](#pairing)。 |

**Docker 安全**：官方 Docker 镜像以非 root 用户（`nanobot`，UID 1000）运行，预装了 bubblewrap。使用 `docker-compose.yml` 时，容器丢弃所有 Linux 能力，除了 `SYS_ADMIN`（bwrap 命名空间隔离所需）。


## 配对

配对让用户通过简单的代码交换获得对 bot 的访问权限——无需编辑配置。这适用于新用户和从新频道连接的现有用户（例如，已在 Telegram 上批准的人现在设置 Discord）。

### 工作原理

1. 用户在他们尚未批准的任何频道（Telegram、Discord、Slack 等）上向 bot 发送私信。
2. bot 回复一个配对代码（如 `ABCD-EFGH`）并告诉他们转发给你。
3. 你批准该代码：

```text
/pairing approve ABCD-EFGH
```

4. 用户现在可以正常与 bot 聊天。

配对仅在**私信**中有效——群聊中未批准的用户会被静默忽略。

### 仅配对模式

默认情况下，如果你没有设置 `allowFrom`，任何尚未批准的人在私信 bot 时都会获得一个配对代码。这意味着你可以完全跳过 `allowFrom` 并通过配对管理所有访问：

```json
{
  "channels": {
    "telegram": {
      "enabled": true
    }
  }
}
```

如果你希望无需批准即可允许所有人：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "allowFrom": ["*"]
    }
  }
}
```

### 管理访问

| 命令 | 作用 |
|---------|-------------|
| `/pairing` | 显示所有待处理的配对请求 |
| `/pairing approve <code>` | 批准请求——发送者现在可以聊天 |
| `/pairing deny <code>` | 拒绝待处理的请求 |
| `/pairing revoke <user_id>` | 从当前频道移除之前批准的用户 |
| `/pairing revoke <channel> <user_id>` | 从特定频道移除用户 |

你可以在 `/pairing list` 的输出中找到用户 ID。

从终端：

```bash
nanobot agent -m "/pairing list"
nanobot agent -m "/pairing approve ABCD-EFGH"
```


## 网关心跳

网关可以运行一个受保护的心跳 cron 作业，定期检查活动工作区中的 `HEARTBEAT.md`。当你运行 `nanobot gateway` 时，这默认启用。

```json
{
  "gateway": {
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800,
      "keepRecentMessages": 8
    }
  }
}
```

如果 `HEARTBEAT.md` 在 `## Active Tasks` 下有任务，agent 会执行它们并将有用的结果交付到最近活动的聊天目标。如果文件没有活动任务，心跳会被静默跳过。

心跳作业由与用户创建的提醒相同的 cron 服务支持。它存储在活动工作区下（`<workspace>/cron/jobs.json`），并在 `cron(action="list")` 中显示为 `heartbeat`，但它是系统管理的，不能用 `cron` 工具移除。如果你不想要定期心跳检查，请通过配置禁用它并重启网关。

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `gateway.heartbeat.enabled` | `true` | 在网关启动时注册内置心跳 cron 作业。 |
| `gateway.heartbeat.intervalS` | `1800` | 心跳检查之间的秒数。 |
| `gateway.heartbeat.keepRecentMessages` | `8` | 每次运行后保留的最近心跳会话消息数。 |


## 子 agent 并发

默认情况下，nanobot 一次只允许一个生成的子 agent。当达到限制时，`spawn` 工具返回错误，以便 agent 可以决定等待或重新安排其工作。这保护本地 LLM 服务器免于一次加载多个 KV 缓存。如果你的 provider 可以处理更多并行工作，请提高限制：

```json
{
  "agents": {
    "defaults": {
      "maxConcurrentSubagents": 2
    }
  }
}
```

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `agents.defaults.maxConcurrentSubagents` | `1` | 可同时运行的生成子 agent 的最大数量。超过此限制的生成尝试会返回错误。 |


## 自动压缩

当用户空闲时间超过配置的阈值时，nanobot 会**主动**将会话上下文的较旧部分压缩为摘要，同时保留最近合法后缀的活动消息。这减少了用户返回时的 token 成本和首 token 延迟——模型不再重新处理具有过期 KV 缓存的冗长陈旧上下文，而是接收紧凑的摘要、最近的活动上下文和新的输入。

```json
{
  "agents": {
    "defaults": {
      "idleCompactAfterMinutes": 15
    }
  }
}
```

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `agents.defaults.idleCompactAfterMinutes` | `15` | 自动压缩开始前的空闲分钟数。设置为 `0` 可禁用。默认值接近典型的 LLM KV 缓存过期窗口，因此陈旧会话会在用户返回之前被压缩。 |

`sessionTtlMinutes` 仍作为旧版别名接受以向后兼容，但 `idleCompactAfterMinutes` 是今后首选的配置键。

工作原理：
1. **空闲检测**：在每个空闲节拍（约 1 秒），检查所有会话是否过期。
2. **后台压缩**：空闲会话通过 LLM 摘要较旧的活动前缀，并保留最近的合法后缀（当前为 8 条消息）。
3. **摘要注入**：当用户返回时，摘要作为运行时上下文（一次性，不持久化）与保留的最近后缀一起注入。
4. **重启安全恢复**：摘要也会镜像到会话元数据中，以便在进程重启后仍可恢复。

> [!NOTE]
> 心智模型："摘要较旧的上下文，保留最新的活动轮次，**并用压缩形式覆盖会话文件。**"它不是完整的 `session.clear()`，但它是一次写入——而不是软光标移动。
>
> 具体而言，自动压缩就地重写 `sessions/<key>.jsonl`：较旧的消息（包括其结构化的 `tool_calls` / `tool_call_id` / `reasoning_content`）仅被保留的最近后缀（当前为 8 条消息）替换，而归档的前缀仅作为纯文本摘要附加到 `memory/history.jsonl`（如果 LLM 摘要失败，则为 `[RAW] ...` 扁平转储）。那些轮次的原始结构化 JSON 不再能从会话文件中恢复。
>
> 这与提示超出上下文预算时触发的**token 驱动的软整合**不同：该路径仅推进内部的 `last_consolidated` 光标，会话文件保持不变，因此原始工具调用轨迹保留在磁盘上，仍可重放或审计。如果你依赖该轨迹进行调试或审计，请将 `idleCompactAfterMinutes` 设置为 `0`，仅让 token 驱动的路径运行。

## 时区

时间是上下文。上下文应该精确。

默认情况下，nanobot 使用 `UTC` 作为运行时时间上下文。如果你希望 agent 以你本地时间思考，请将 `agents.defaults.timezone` 设置为有效的 [IANA 时区名称](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)：

```json
{
  "agents": {
    "defaults": {
      "timezone": "Asia/Shanghai"
    }
  }
}
```

这会影响显示给模型的运行时时间字符串，例如运行时上下文。当 cron 表达式省略 `tz` 时，它也成为 cron 调度的默认时区，当 ISO 日期时间没有显式偏移时，它也是一次性 `at` 时间的默认时区。

常见示例：`UTC`、`America/New_York`、`America/Los_Angeles`、`Europe/London`、`Europe/Berlin`、`Asia/Tokyo`、`Asia/Shanghai`、`Asia/Singapore`、`Australia/Sydney`。

> 需要另一个时区？浏览完整的 [IANA 时区数据库](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)。

## 统一会话

默认情况下，每个频道 × 聊天 ID 组合都有自己的会话。如果你跨多个频道使用 nanobot（例如 Telegram + Discord + CLI）并希望它们共享同一个对话，请启用 `unifiedSession`：

```json
{
  "agents": {
    "defaults": {
      "unifiedSession": true
    }
  }
}
```

启用后，所有传入消息——无论到达哪个频道——都被路由到单个共享会话。从 Telegram 切换到 Discord（或任何其他频道）可以无缝继续同一对话。

| 行为 | `false`（默认） | `true` |
|----------|-------------------|--------|
| 会话键 | `channel:chat_id` | `unified:default` |
| 跨频道连续性 | 否 | 是 |
| `/new` 清除 | 当前频道会话 | 共享会话 |
| `/stop` 查找任务 | 按频道会话 | 按共享会话 |
| 现有 `session_key_override`（例如 Telegram 线程） | 受尊重 | 仍受尊重——不被覆盖 |

> 这专为单用户、多设备设置设计。它**默认关闭**——现有用户看不到任何行为变化。

## 禁用的技能

nanobot 附带内置技能，你的工作区也可以在 `skills/` 下定义自定义技能。如果你想向 agent 隐藏特定技能，请将 `agents.defaults.disabledSkills` 设置为技能目录名称列表：

```json
{
  "agents": {
    "defaults": {
      "disabledSkills": ["github", "weather"]
    }
  }
}
```

禁用的技能会从主 agent 的技能摘要、始终开启的技能注入和子 agent 技能摘要中排除。当某些捆绑技能对你的部署不必要或不应暴露给最终用户时，这很有用。

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `agents.defaults.disabledSkills` | `[]` | 要排除加载的技能目录名称列表。适用于内置技能和工作区技能。 |

## 工具提示最大长度

工具提示是 agent 调用工具时显示的简短进度消息（例如 `$ cd …/project && npm test`）。默认情况下，这些在 40 个字符处截断，这会使长命令难以阅读。

设置 `agents.defaults.toolHintMaxLength` 来控制截断阈值：

```json
{
  "agents": {
    "defaults": {
      "toolHintMaxLength": 120
    }
  }
}
```

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `agents.defaults.toolHintMaxLength` | `40` | 工具提示显示的最大字符数。范围：20–500。较高的值显示更多命令或路径；较低的值保持提示紧凑。 |
