"""多用户多会话并发测试 chatbot。

与 `chatbot/` 的单用户视图不同，本包提供：
- 左侧：可编辑的「用户 → 会话 → 预设提问」三层树，持久化到磁盘
- 中间：按 (用户, 会话) 同时排列多个独立聊天窗口（2 列网格）
- 右侧：活跃并发数 / 排队阻塞数 / 车道列表，用来观察 OpenClaw
  `FairScheduler` 在多路复用时的真实负载
"""
from .config_store import DEFAULT_PATH, MultiConfigStore

__all__ = ["DEFAULT_PATH", "MultiConfigStore"]
