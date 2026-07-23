"""Shared WebUI metadata keys.

定义 WebUI 侧在消息/会话元数据中使用的共享键名，供多模块统一引用，
避免键名拼写不一致。``WEBUI_TURN_METADATA_KEY`` 标识一轮交互以关联同轮多条消息；
以 ``_`` 开头的 ``WEBUI_MESSAGE_SOURCE_METADATA_KEY`` 表示内部用途，不对外暴露。
"""

WEBUI_TURN_METADATA_KEY = "webui_turn_id"
WEBUI_MESSAGE_SOURCE_METADATA_KEY = "_webui_message_source"
