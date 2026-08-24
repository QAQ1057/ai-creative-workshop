import os
import sys

# 把项目根目录加入模块搜索路径，保证 `import app` 能正确加载根目录的 app.py
# 说明：Vercel 构建时 /api 与根目录的相对位置是固定的，此写法在本地与云端均成立
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 导入根目录 Flask 应用实例（同时会执行 app.py 顶部的初始化逻辑）
from app import app  # noqa: E402

# Vercel 约定：导出的 `app` 即 WSGI 入口
app = app
