# 开发

本页面收集面向贡献者的扩展 nanobot 相关说明。面向用户的设置和运行时选项位于 [`configuration.md`](./configuration.md)。

## 添加 LLM Provider

nanobot 使用 `nanobot/providers/registry.py` 中的 provider 注册表作为 LLM provider 元数据的权威来源。大多数 OpenAI 兼容的 provider 只需两处改动。

1. 在 `PROVIDERS` 中添加一个 `ProviderSpec` 条目：

```python
ProviderSpec(
    name="myprovider",
    keywords=("myprovider", "mymodel"),
    env_key="MYPROVIDER_API_KEY",
    display_name="My Provider",
    default_api_base="https://api.myprovider.com/v1",
)
```

2. 在 `nanobot/config/schema.py` 中为 `ProvidersConfig` 添加一个字段：

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = Field(default_factory=ProviderConfig)
```

环境变量、配置匹配、provider 状态和 WebUI 凭据显示都派生自这两个条目。

实用的 `ProviderSpec` 选项：

| 字段 | 描述 |
|---|---|
| `default_api_base` | 默认的 OpenAI 兼容 base URL。 |
| `env_extras` | 从 provider 配置派生的附加环境变量。 |
| `model_overrides` | 按模型的请求参数覆盖。 |
| `is_gateway` | Provider 可路由多个模型系列，例如 OpenRouter。 |
| `detect_by_key_prefix` | 按 API key 前缀匹配已配置的网关。 |
| `detect_by_base_keyword` | 按 API base URL 匹配已配置的网关。 |
| `strip_model_prefix` | 在将模型发送给上游 API 之前去除 `provider/` 前缀。 |
| `supports_max_completion_tokens` | 使用 `max_completion_tokens` 而非 `max_tokens`。 |
| `is_transcription_only` | Provider 拥有凭据但无法提供聊天补全。 |

## 添加转录 Provider

转录被有意分为两层：

- `nanobot/audio/transcription_registry.py` 拥有 provider 名称、别名、默认模型和 adapter 加载。
- `nanobot/providers/transcription.py` 拥有 provider 特定的 HTTP 行为。

凭据仍存放在 `providers.<provider>` 下，以便聊天渠道和 WebUI 以相同方式解析 API key 和 API base。

1. 将 provider 凭据添加到 `ProvidersConfig`。

```python
class ProvidersConfig(BaseModel):
    ...
    my_stt: ProviderConfig = Field(default_factory=ProviderConfig)
```

2. 在 `nanobot/providers/registry.py` 中添加一个 `ProviderSpec`。

对于仅转录的 provider，设置 `is_transcription_only=True`，使其出现在凭据/设置界面中但不进入聊天模型选择。

```python
ProviderSpec(
    name="my_stt",
    keywords=("my_stt",),
    env_key="MY_STT_API_KEY",
    display_name="My STT",
    default_api_base="https://api.example.com/v1",
    is_transcription_only=True,
)
```

3. 在 `nanobot/providers/transcription.py` 中添加一个 adapter 类。

adapter 接收已解析的凭据和设置。它们在 provider 出错时返回空字符串，以便渠道语音消息能安静地失败，而不是导致 agent 循环崩溃。

```python
class MySTTTranscriptionProvider:
    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("MY_STT_API_KEY")
        self.api_base = api_base or "https://api.example.com/v1"
        self.language = language or None
        self.model = model or "my-default-stt-model"

    async def transcribe(self, file_path: str | Path) -> str:
        ...
```

4. 在 `nanobot/audio/transcription_registry.py` 中注册该 adapter。

```python
TranscriptionProviderSpec(
    name="my_stt",
    default_model="my-default-stt-model",
    adapter="nanobot.providers.transcription:MySTTTranscriptionProvider",
    aliases=("mystt",),
)
```

5. 添加测试。

至少覆盖：

- `tests/providers/test_transcription.py` 中的配置解析
- adapter 的请求/响应行为以及重试/错误处理
- `tests/webui/test_settings_api.py` 中的 WebUI 设置载荷/更新行为
- 如果该 provider 出现在设置中，还需覆盖 provider 品牌映射

6. 更新面向用户的文档。

将该 provider 添加到 [`configuration.md`](./configuration.md) 中用户选择 `transcription.provider` 的位置，但将实现细节保留在本开发指南中。
