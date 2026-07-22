"""Helpers for runtime model preset selection.

"模型预设"（model preset）助手。一个预设把一组模型参数（model、context_window_tokens、
生成参数等）打包成一个命名配置。运行时可按名称切换预设，从而动态切换模型/参数，
而无需重启。本模块提供预设的解析、快照构建与加载器封装。
"快照"（ProviderSnapshot）是预设应用后得到的 (provider, model, context_window_tokens) 元组。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMProvider
from nanobot.providers.factory import ProviderSnapshot, build_provider_snapshot

PresetSnapshotLoader = Callable[[str], ProviderSnapshot]


def default_selection_signature(signature: tuple[object, ...] | None) -> tuple[object, ...] | None:
    # 取 provider 签名的前两项作为"默认选择签名"，用于检测默认 provider/模型是否变化。
    return signature[:2] if signature else None


def configured_model_presets(config: Any) -> dict[str, ModelPresetConfig]:
    # 合并用户配置的预设与一个 "default" 预设（来自配置的默认解析）。
    return {**config.model_presets, "default": config.resolve_default_preset()}


def make_preset_snapshot_loader(
    config: Any,
    provider_snapshot_loader: Callable[..., ProviderSnapshot] | None,
) -> PresetSnapshotLoader:
    # 构造预设快照加载器：优先用外部传入的 provider_snapshot_loader，否则用 build_provider_snapshot。
    if provider_snapshot_loader is not None:
        return lambda name: provider_snapshot_loader(preset_name=name)
    return lambda name: build_provider_snapshot(config, preset_name=name)


def build_static_preset_snapshot(
    provider: LLMProvider,
    name: str,
    preset: ModelPresetConfig,
) -> ProviderSnapshot:
    # 用预设的生成参数就地更新 provider，并构造一个带预设签名的快照。
    provider.generation = preset.to_generation_settings()
    return ProviderSnapshot(
        provider=provider,
        model=preset.model,
        context_window_tokens=preset.context_window_tokens,
        signature=("model_preset", name, preset.model_dump_json()),
    )


def build_runtime_preset_snapshot(
    *,
    name: str,
    presets: dict[str, ModelPresetConfig],
    provider: LLMProvider,
    loader: PresetSnapshotLoader | None,
) -> ProviderSnapshot:
    # 运行时构建预设快照：有 loader 则用 loader（可能动态读取最新配置），否则静态构建。
    if loader is not None:
        return loader(name)
    return build_static_preset_snapshot(provider, name, presets[name])


def normalize_preset_name(name: str | None, presets: dict[str, ModelPresetConfig]) -> str:
    # 校验并归一化预设名：非空、必须存在于已配置预设中，否则抛错。
    if not isinstance(name, str) or not name.strip():
        raise ValueError("model_preset must be a non-empty string")
    name = name.strip()
    if name not in presets:
        raise KeyError(f"model_preset {name!r} not found. Available: {', '.join(presets) or '(none)'}")
    return name

