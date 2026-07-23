"""Lightweight skill summaries for the WebUI."""

# 为 WebUI 提供技能（skill）的轻量级摘要与详情负载。核心职责是从 `SkillsLoader`
# 读取技能元数据，组装成不含本地文件系统路径的安全负载返回前端，避免泄露服务器端
# 目录结构。列表与详情两套接口分别对应前端技能选择器与技能详情页。

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.skills import SkillsLoader


def webui_skills_payload(
    workspace_path: Path,
    *,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Return agent skills without leaking local filesystem paths."""
    # 返回技能列表负载。排序键先把工作区技能排在前（source=="workspace" 时元组首元素为
    # False=0），再按名称字母序，让用户自定义技能优先于内置技能展示。
    # filter_unavailable=False 表示不可用技能也列出（前端据此显示原因），交由 payload 标记。
    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    entries = sorted(
        loader.list_skills(filter_unavailable=False),
        key=lambda entry: (entry.get("source") != "workspace", entry["name"]),
    )
    return {"skills": [_skill_payload(loader, entry) for entry in entries]}


def webui_skill_detail_payload(
    workspace_path: Path,
    name: str,
    *,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any] | None:
    """Return a single skill's safe detail payload."""
    # 返回单个技能的详情负载：在列表项基础上额外附带依赖声明（requirements）与原始
    # markdown 正文（raw_markdown），供前端渲染技能完整说明。找不到则返回 None。
    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    entries = loader.list_skills(filter_unavailable=False)
    entry = next((item for item in entries if item["name"] == name), None)
    if entry is None:
        return None
    return {
        **_skill_payload(loader, entry),
        "requirements": loader.get_skill_requirements(name),
        "raw_markdown": loader.load_skill(name) or "",
    }


def _skill_payload(loader: SkillsLoader, entry: dict[str, str]) -> dict[str, Any]:
    # 组装单个技能的安全摘要：只暴露 name/description/source/可用性，刻意不包含任何
    # 文件路径。available 与 unavailable_reason 让前端能区分"禁用/缺依赖/可用"等状态。
    name = entry["name"]
    metadata = loader.get_skill_metadata(name)
    available, unavailable_reason = loader.get_skill_availability(name)
    return {
        "name": name,
        "description": _description(metadata, name),
        "source": entry.get("source", "unknown"),
        "available": available,
        "unavailable_reason": unavailable_reason,
    }


def _description(metadata: dict[str, Any] | None, fallback: str) -> str:
    # 取技能描述：优先用元数据中的 description，为空或非字符串时退回技能名作为兜底，
    # 保证列表项始终有可读文本。
    if metadata is None:
        return fallback
    value = metadata.get("description")
    return value.strip() if isinstance(value, str) and value.strip() else fallback
