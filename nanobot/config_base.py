"""Shared Pydantic base model for configuration DTOs.

所有配置数据传输对象（DTO）共享的 Pydantic 基类。

This module intentionally lives outside the ``nanobot.config`` package so
runtime modules can define local config DTOs without importing the full root
configuration schema.
该模块故意放在 ``nanobot.config`` 包之外，这样运行时模块可以定义自己的
局部配置 DTO，而不会引入完整的根配置 schema（避免循环导入）。
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys.

    配置基类：同时接受 camelCase 与 snake_case 两种键名。
    JSON 配置文件通常使用 camelCase（与 JS 生态一致），而 Python 代码内部
    使用 snake_case；通过 ``alias_generator=to_camel`` 自动把 snake_case
    字段映射为 camelCase 别名，配合 ``populate_by_name=True`` 让两种写法都生效。
    """

    # populate_by_name=True: 允许用字段原名（snake_case）赋值；
    # alias_generator=to_camel: 自动为每个字段生成 camelCase 别名供反序列化使用。
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
