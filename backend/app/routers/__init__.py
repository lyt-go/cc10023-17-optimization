"""业务路由汇总：洞察归并等固定路径优先注册，再挂载通用资源路由。"""
from __future__ import annotations

from app.routers import insight, resource

ROUTERS = [insight, resource]
