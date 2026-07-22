# 图像生成

nanobot 可以通过 `generate_image` 工具生成和编辑图像。在 WebUI 中，用户可以从输入框启用 **Image Generation**，选择宽高比，并在同一会话中持续迭代已生成的图像。

该功能默认关闭。请在 `~/.nanobot/config.json` 中启用它，配置一个受支持的图像 provider，然后重启网关。

## 快速设置

以下代码片段使用了当前内置的图像生成默认值，以便 JSON 中包含具体的名称。这并非 provider 推荐；请将 `provider` 和 `model` 替换为你打算使用的任意受支持的图像 provider 和模型。

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "openrouter",
      "model": "openai/gpt-5.4-image-2"
    }
  }
}
```

关于 Custom、AIHubMix、MiniMax、Gemini、Ollama、StepFun 和 Zhipu 的配置示例，请参见 [Provider 说明](#provider-notes)。

> [!TIP]
> 推荐使用环境变量存放 API 密钥。nanobot 会在启动时从环境变量中解析 `${VAR_NAME}` 的值。

## WebUI 用法

在 WebUI 输入框中：

1. 点击 **Image Generation**。
2. 选择宽高比：`Auto`、`1:1`、`3:4`、`9:16`、`4:3` 或 `16:9`。
3. 描述你想要的图像或编辑内容。
4. 在编辑已有图像时附加参考图像。

生成的图像会作为助手媒体内容渲染在会话中。后续提示词，例如“让它更暖一些”、“更换背景”或“试试 16:9 版本”，可以复用最近生成的制品。

WebUI 对用户隐藏了 provider 的存储细节。Agent 在内部会看到已保存的制品路径，并可以将其作为 `reference_images` 传回 `generate_image`，用于迭代编辑。

## 配置参考

| 选项 | 类型 | 默认值 | 说明 |
|--------|------|---------|-------------|
| `tools.imageGeneration.enabled` | 布尔值 | `false` | 注册 `generate_image` 工具 |
| `tools.imageGeneration.provider` | 字符串 | `"openrouter"` | 当前内置图像 provider 默认值。受支持的值：`openrouter`、`openai`、`openai_codex`、`custom`、`aihubmix`、`minimax`、`gemini`、`ollama`、`stepfun`、`zhipu` |
| `tools.imageGeneration.model` | 字符串 | `"openai/gpt-5.4-image-2"` | Provider 模型名称 |
| `tools.imageGeneration.defaultAspectRatio` | 字符串 | `"1:1"` | 当提示词/工具调用未指定时的默认宽高比 |
| `tools.imageGeneration.defaultImageSize` | 字符串 | `"1K"` | 默认尺寸提示，例如 `1K`、`2K`、`4K` 或 `1024x1024` |
| `tools.imageGeneration.maxImagesPerTurn` | 数字 | `4` | 一次工具调用可接受的最大 `count`。有效范围：`1` 到 `8` |
| `tools.imageGeneration.saveDir` | 字符串 | `"generated"` | nanobot 媒体目录下用于存放生成制品的相对目录 |

Provider 设置复用常规的 provider 配置字段：

| 选项 | 说明 |
|--------|-------------|
| `providers.<name>.apiKey` | Provider API 密钥。推荐使用 `${ENV_VAR}` |
| `providers.<name>.apiBase` | 可选的自定义 base URL |
| `providers.<name>.extraHeaders` | 合并到 provider 请求中的请求头 |
| `providers.<name>.extraBody` | 合并到 provider 请求体中的额外 JSON 字段 |

camelCase 和 snake_case 配置键均被接受，但文档使用 camelCase 以与 `config.json` 保持一致。

## Provider 说明

### OpenRouter

OpenRouter 使用类似 chat-completions 的图像响应。配置如下：

```json
{
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "openrouter",
      "model": "openai/gpt-5.4-image-2"
    }
  }
}
```

如果你需要参考图像编辑，请使用支持图像生成和图像编辑的模型。

### Custom（OpenAI 兼容）

`custom` 图像 provider 适用于实现了同步 OpenAI Images API 的服务：

```text
POST /v1/images/generations
```

响应必须将生成的图像包含在 `data[].b64_json` 或 `data[].url` 中。原生预测 API，例如 Replicate 的 `/v1/models/{owner}/{model}/predictions`，并不直接兼容，除非你在它们前面放置一个 OpenAI 兼容的网关。

配置如下：

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_IMAGE_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "custom",
      "model": "your-model-name"
    }
  }
}
```

`apiBase` 是必需的。该 provider 会使用 OpenAI Images API 格式（带 `response_format: "b64_json"`）向 `{apiBase}/images/generations` 发送请求。对于本地或无需鉴权的端点，`apiKey` 是可选的。通用的 `custom` provider 不支持参考图像编辑。

`extraBody` 可以适配 provider 特有的行为差异，因为它会在最后合并到请求体中。示例：

- Agnes AI 的文档说明其响应为 URL，因此使用 `"extraBody": {"response_format": "url"}`。
- Together AI 的文档使用 `"response_format": "base64"`，因此需覆盖默认值。
- 火山引擎 Ark Seedream 模型可能需要尺寸提示，例如 `"2K"`、`"3K"`、`"4K"`，或显式尺寸。请将 `tools.imageGeneration.defaultImageSize` 或 `providers.custom.extraBody.size` 设置为所选模型支持的值。

为了与 nanobot 的默认设置兼容，custom 会将 `defaultImageSize: "1K"` 映射为 `1024x1024`。其他显式尺寸提示则原样传递。

### AIHubMix

AIHubMix 的 `gpt-image-2-free` 通过 AIHubMix 的统一预测 API 受到支持。在内部，nanobot 会调用：

```text
/v1/models/openai/gpt-image-2-free/predictions
```

配置如下：

```json
{
  "providers": {
    "aihubmix": {
      "apiKey": "${AIHUBMIX_API_KEY}",
      "extraBody": {
        "quality": "low"
      }
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "aihubmix",
      "model": "gpt-image-2-free"
    }
  }
}
```

`quality: low` 是可选的。它可以让免费图像模型更快、更不容易超时，但并非正确性所必需。

### MiniMax

MiniMax 的 `image-01` 支持文本生成图像以及参考图像（主体参考）编辑。受支持的宽高比为 `1:1`、`16:9`、`4:3`、`3:2`、`2:3`、`3:4`、`9:16` 和 `21:9`。

```json
{
  "providers": {
    "minimax": {
      "apiKey": "${MINIMAX_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "minimax",
      "model": "image-01",
      "defaultAspectRatio": "1:1"
    }
  }
}
```

### Gemini

nanobot 通过 Google 的 Generative Language API 支持两个 Gemini 图像生成模型家族：

| 模型 | 端点 | 参考图像 |
|-------|----------|-----------------|
| `imagen-4.0-generate-001` | `:predict` | 此集成不支持 |
| `gemini-2.5-flash-image` | `:generateContent` | 支持 |

如需参考图像编辑，请使用 Gemini Flash 图像模型：

```json
{
  "providers": {
    "gemini": {
      "apiKey": "${GEMINI_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "gemini",
      "model": "gemini-2.5-flash-image"
    }
  }
}
```

Imagen 4 支持宽高比 `1:1`、`9:16`、`16:9`、`3:4` 和 `4:3`。不支持的宽高比会被忽略，模型会使用其默认值。`defaultImageSize` 设置对 Gemini 模型无效；尺寸仅由 `defaultAspectRatio` 控制。与 Imagen 模型一起传入的参考图像会被忽略（并记录一条警告日志）。

### Ollama

Ollama 的实验性原生图像生成 API 可与本地服务器以及托管的 ollama.com 模型配合使用。在 `http://localhost:11434/api` 的本地访问不需要 API 密钥；仅当目标是 `https://ollama.com/api` 时才需设置 `providers.ollama.apiKey`。

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/api"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "ollama",
      "model": "x/z-image-turbo",
      "defaultAspectRatio": "16:9",
      "defaultImageSize": "2K"
    }
  }
}
```

Ollama 会将 `defaultAspectRatio` 和 `defaultImageSize` 映射为原生的 `width` 和 `height` 值。此集成不支持参考图像。

### StepFun

StepFun（阶跃星辰）的 `step-image-edit-2` 支持文本生成图像。`step-1x-medium` 变体还支持 **风格参考（style-reference）** 图像编辑，即由参考图像引导输出的视觉风格。

受支持的宽高比：`1:1`、`16:9`、`9:16`、`3:4`、`4:3`。尺寸以 `WIDTHxHEIGHT` 形式指定（例如 `1024x1024`、`1280x800`、`800x1280`）。

```json
{
  "providers": {
    "stepfun": {
      "apiKey": "${STEPFUN_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "stepfun",
      "model": "step-image-edit-2"
    }
  }
}
```

> [!NOTE]
> StepFun provider 复用现有的 `providers.stepfun` 配置块（与 StepFun 的 LLM API 所使用的相同）。只需设置一次 `providers.stepfun.apiKey`，即可在文本生成与图像生成之间共享。
>
> 当使用 `step-image-edit-2` 时，`reference_images` 会被忽略（该模型不支持风格参考）。如需使用参考图像引导的生成，请切换到 `step-1x-medium`。

#### StepPlan（订阅版）

StepPlan 是 StepFun 的订阅层级，使用不同的 API base URL。图像生成端点路径相同——只需覆盖 `apiBase`：

```json
{
  "providers": {
    "stepfun": {
      "apiKey": "${STEPFUN_API_KEY}",
      "apiBase": "https://api.stepfun.ai/step_plan/v1"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "stepfun",
      "model": "step-image-edit-2"
    }
  }
}
```

`apiBase` 的优先级高于注册表默认值，因此在配置了 StepPlan 的 base URL 后，图像请求会发送到 `https://api.stepfun.ai/step_plan/v1/images/generations`——与 LLM 调用使用的路径前缀相同。API 密钥与标准 StepFun provider 共享。

### Zhipu

Zhipu（智谱）的 `glm-image` 模型支持文本生成图像。该 API 返回临时图像 URL（有效期为 30 天）；nanobot 会下载并将其重新编码为 base64 data URL。

受支持的宽高比：`1:1`、`16:9`、`9:16`、`3:4`、`4:3`。尺寸可以以 `WIDTHxHEIGHT` 形式指定（例如 `1280x1280`、`1728x960`），或使用宽高比预设。

```json
{
  "providers": {
    "zhipu": {
      "apiKey": "${ZAI_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "zhipu",
      "model": "glm-image"
    }
  }
}
```

其他受支持的模型：`cogview-4`、`cogview-4-250304`、`cogview-3-flash`。此集成不支持参考图像。

## 制品

生成的图像存放在当前活动的 nanobot 实例的媒体目录下：

```text
~/.nanobot/media/generated/YYYY-MM-DD/img_<id>.<ext>
~/.nanobot/media/generated/YYYY-MM-DD/img_<id>.json
```

对于非默认的配置位置，媒体目录相对于当前活动的配置文件所在目录。

JSON 附属文件存储以下内容：

| 字段 | 含义 |
|-------|---------|
| `id` | 短生成的图像 ID，例如 `img_ab12cd34ef56` |
| `path` | 内部用于后续编辑的本地图像路径 |
| `mime` | 检测到的图像 MIME 类型 |
| `prompt` | 用于生成的提示词 |
| `model` | Provider 模型 |
| `provider` | Provider 名称 |
| `source_images` | 用于编辑的参考图像路径 |
| `created_at` | 创建时间戳 |

请勿将 base64 图像载荷粘贴到会话中。Agent 应将本地制品路径保持在内部，除非用户明确要求查看调试细节。

## 提示词

好的图像提示词应包含：

- 主体和场景。
- 构图、镜头或布局。
- 风格、氛围、光照和配色。
- 必须出现在图像中的确切文字，需加引号。
- 约束条件，例如“保持同一角色”或“保留 logo”。

示例：

```text
A minimal app icon for nanobot: friendly robot head, rounded square, soft blue and white palette, clean vector style, no text
```

对于编辑，请描述应当改变的部分以及必须保持不变的部分：

```text
Use the reference image. Keep the same robot and composition, change the palette to warm orange, and add a subtle sunrise background.
```

## 故障排查

| 症状 | 检查项 |
|---------|-------|
| `generate_image` 不可用 | 将 `tools.imageGeneration.enabled` 设置为 `true` 并重启网关 |
| 缺少 API 密钥错误 | 配置 `providers.<provider>.apiKey`；如果使用 `${VAR_NAME}`，请确认环境变量对网关进程可见 |
| `unsupported image generation provider` | 使用 `openrouter`、`openai`、`openai_codex`、`custom`、`aihubmix`、`minimax`、`gemini`、`ollama`、`stepfun` 或 `zhipu` |
| AIHubMix 提示 `Incorrect model ID` | 使用 `model: "gpt-image-2-free"`；nanobot 会在内部将其展开为所需的 `openai/gpt-image-2-free` 模型路径 |
| 生成超时 | 尝试更小/默认的图像尺寸，将 AIHubMix 的 `extraBody.quality` 设置为 `"low"`，或稍后重试 |
| 参考图像被拒绝 | 参考图像路径必须位于工作区或 nanobot 媒体目录内，且必须是有效的图像文件 |
