import os
import time

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------
# 支持的图像生成 API 端点
DOUBAO_IMAGE_API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
OPENAI_IMAGE_API_URL = "https://api.openai.com/v1/images/generations"
DEEPSEEK_IMAGE_API_URL = "https://api.deepseek.com/v1/image/generate"

# 默认 provider 与模型（豆包 Seedream 5.0 Pro 文生图）
DEFAULT_IMAGE_API_PROVIDER = "doubao"
DEFAULT_IMAGE_API_MODEL = "doubao-seedream-5-0-pro-260628"

# 上游请求超时时间（秒）：连接 + 读取。
# 注意：Seedream 5.0 Pro 实测单图生成约 50-60 秒，超时需留足余量
REQUEST_TIMEOUT_SECONDS = 120

# 单次请求允许的最大提示词长度（字符），防止恶意超大请求
MAX_PROMPT_LENGTH = 2000

# 项目根目录，用于本地开发时提供静态文件
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Flask 应用初始化
# ---------------------------------------------------------------------------
app = Flask(__name__)

# 允许跨域：本地开发时前端可能运行在独立端口（如 5001）或 file:// 协议下
# 生产环境（同域部署）下 CORS 不会带来额外风险
CORS(app)

# 本地开发时自动加载项目根目录的 .env 文件
# Vercel 部署时环境变量由平台注入，不会读取 .env
load_dotenv()


def get_api_key() -> str | None:
    return (
        os.environ.get("IMAGE_API_KEY", "").strip()
        or os.environ.get("ARK_API_KEY", "").strip()
        or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or None
    )


def get_image_api_config() -> dict:
    provider = os.environ.get("IMAGE_API_PROVIDER", DEFAULT_IMAGE_API_PROVIDER).strip().lower()
    model = os.environ.get("IMAGE_API_MODEL", DEFAULT_IMAGE_API_MODEL).strip()

    if provider == "doubao":
        # 豆包（火山方舟 Ark）：
        # - 必须传 model（模型 ID 或接入点 ID ep-xxx）；
        # - response_format=url 让服务端返回图片直链；
        # - watermark=false 关闭官方水印；
        # - 多图生成走 sequential_image_generation，不支持 n 参数。
        return {
            "provider": provider,
            "url": DOUBAO_IMAGE_API_URL,
            "model": model,
            "extra_payload": {
                "model": model,
                "response_format": "url",
                "watermark": False,
            },
            "send_n": False,
        }

    if provider == "deepseek":
        return {
            "provider": provider,
            "url": DEEPSEEK_IMAGE_API_URL,
            "model": model,
            "extra_payload": {},
            "send_n": True,
        }

    # 默认使用 OpenAI 兼容格式（DALL·E 3）
    return {
        "provider": provider,
        "url": OPENAI_IMAGE_API_URL,
        "model": model,
        "extra_payload": {"model": model},
        "send_n": True,
    }


def build_error_response(message: str, status_code: int):
    return jsonify({"success": False, "error": {"message": message}}), status_code


# ---------------------------------------------------------------------------
# 路由：前端静态资源（仅本地开发需要；Vercel 会自动托管根目录静态文件）
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    """返回单页应用入口 index.html。"""
    return send_from_directory(ROOT_DIR, "index.html")


@app.get("/manifest.json")
def manifest():
    """返回 PWA manifest 文件。"""
    return send_from_directory(ROOT_DIR, "manifest.json")


@app.get("/sw.js")
def service_worker():
    """返回 Service Worker 脚本，注意设置正确的 MIME 类型。"""
    response = send_from_directory(ROOT_DIR, "sw.js")
    response.headers["Content-Type"] = "application/javascript"
    return response


@app.get("/icons/<path:filename>")
def icons(filename: str):
    """返回 PWA 图标。"""
    return send_from_directory(os.path.join(ROOT_DIR, "icons"), filename)


# ---------------------------------------------------------------------------
# 路由：健康检查
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """健康检查接口，供前端 / 监控系统探测服务是否存活。

    Returns:
        服务状态 JSON
    """
    config = get_image_api_config()
    return jsonify(
        {
            "success": True,
            "data": {
                "service": "ai-creative-workshop",
                "status": "ok",
                "api_key_configured": get_api_key() is not None,
                "provider": config["provider"],
                "model": config["model"],
                "timestamp": int(time.time()),
            },
        }
    )


# ---------------------------------------------------------------------------
# 路由：图像生成（核心转发逻辑）
# ---------------------------------------------------------------------------
@app.post("/api/generate")
def generate_image():
    # ------------------------- 1. 校验请求参数 -------------------------
    body = request.get_json(silent=True) or {}

    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        return build_error_response("请输入图像描述（prompt）", 400)
    if len(prompt) > MAX_PROMPT_LENGTH:
        return build_error_response(f"描述过长，请控制在 {MAX_PROMPT_LENGTH} 字以内", 400)

    size = str(body.get("size") or "1024x1024").strip()
    n = int(body.get("n") or 1)

    # ------------------------- 2. 检查 API Key --------------------------
    api_key = get_api_key()
    if api_key is None:
        # 服务端未配置密钥：提示部署者，而不是前端用户
        return build_error_response(
            "服务端未配置 IMAGE_API_KEY，请联系管理员在环境变量中设置", 500
        )

    # ------------------------- 3. 构造上游请求 --------------------------
    config = get_image_api_config()

    # 关键安全点：API Key 只放在服务端出站请求的 Header 中，
    # 永远不会回传给浏览器，也不会出现在任何日志里
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "size": size,
        **config["extra_payload"],
    }
    # 生成数量参数仅对支持 n 的 provider 生效（如 OpenAI）
    if config.get("send_n") and n > 1:
        payload["n"] = n

    start_time = time.time()
    try:
        upstream_resp = requests.post(
            config["url"],
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        # 上游超时（连接超时或读取超时）
        return build_error_response("图像生成超时，请稍后重试", 504)
    except requests.exceptions.ConnectionError:
        # 网络不可达（域名解析失败 / 连接被拒绝等）
        return build_error_response("无法连接图像服务，请检查网络后重试", 502)
    except requests.exceptions.RequestException as exc:
        # 其他请求层异常（兜底）
        app.logger.error("图像 API 请求异常: %s", exc)
        return build_error_response("图像服务暂时不可用，请稍后重试", 502)

    # ------------------------- 4. 解析上游响应 --------------------------
    elapsed = round(time.time() - start_time, 2)

    if upstream_resp.status_code != 200:
        return handle_upstream_error(upstream_resp)

    try:
        upstream_data = upstream_resp.json()
    except ValueError:
        # 上游返回 200 但内容不是合法 JSON，按服务异常处理
        app.logger.error("图像 API 返回非法 JSON: %s", upstream_resp.text[:500])
        return build_error_response("图像服务响应异常，请稍后重试", 502)

    image_url = extract_image_url(upstream_data)
    if image_url is None:
        return build_error_response("图像服务未返回有效图片数据", 502)

    return jsonify(
        {
            "success": True,
            "data": {
                "image_url": image_url,
                "prompt": prompt,
                "elapsed_seconds": elapsed,
            },
        }
    )


def handle_upstream_error(upstream_resp: requests.Response):
    """把上游错误码翻译成用户可理解的中文提示。

    Args:
        upstream_resp: 上游非 200 响应

    Returns:
        (Flask Response, HTTP 状态码)
    """
    status = upstream_resp.status_code

    # 尝试读取上游返回的错误码与错误信息，便于调试（只记录，不外泄敏感信息）
    upstream_msg = ""
    upstream_code = ""
    try:
        upstream_error = upstream_resp.json().get("error", {})
        upstream_msg = upstream_error.get("message", "")
        upstream_code = upstream_error.get("code", "")
    except ValueError:
        pass

    # 豆包（火山方舟）特有错误：账户未开通对应模型服务
    if upstream_code == "ModelNotOpen" or "has not activated the model" in upstream_msg:
        return build_error_response(
            "豆包模型未开通：请到火山方舟控制台「开通管理」中开通该图像生成模型后再试",
            502,
        )
    # 模型 ID / 接入点不存在（可能填错了 IMAGE_API_MODEL）
    if upstream_code == "InvalidEndpointOrModel.NotFound":
        return build_error_response(
            "模型或接入点不存在：请检查 IMAGE_API_MODEL 配置是否正确",
            502,
        )

    if status == 401 or status == 403:
        # 密钥无效 / 无权限
        return build_error_response("API Key 无效或已过期，请联系管理员检查配置", 401)
    if status == 429:
        # 触发限流（额度不足或请求过于频繁）
        return build_error_response("请求过于频繁或额度不足，请稍后再试", 429)
    if status >= 500:
        # 上游服务内部错误
        return build_error_response("图像服务繁忙，请稍后重试", 502)

    # 其他未预期的错误码，附带上游信息便于排查
    app.logger.error("图像 API 返回状态码 %s: %s", status, upstream_msg)
    return build_error_response("图像生成失败，请稍后重试", 502)


def extract_image_url(data: dict) -> str | None:
    # 格式 1 & 2：OpenAI 风格 data 数组
    data_list = data.get("data")
    if isinstance(data_list, list) and data_list:
        first = data_list[0] if isinstance(data_list[0], dict) else {}
        url = first.get("url")
        if url:
            return str(url)
        b64 = first.get("b64_json")
        if b64:
            return data_url_from_base64(str(b64))

    # 格式 3：images 字符串数组
    images = data.get("images")
    if isinstance(images, list) and images and isinstance(images[0], str):
        return images[0]

    # 格式 4：扁平 image_url 字段
    flat_url = data.get("image_url")
    if flat_url:
        return str(flat_url)

    return None


def data_url_from_base64(b64_text: str) -> str:
    """把 base64 图片数据包装成 data URL（便于前端直接 <img> 展示）。

    Args:
        b64_text: base64 编码的图片数据

    Returns:
        形如 data:image/png;base64,xxxx 的 Data URL 字符串
    """
    # 去掉可能存在的 data URL 前缀，避免重复包装
    if b64_text.startswith("data:"):
        return b64_text
    return f"data:image/png;base64,{b64_text}"


# ---------------------------------------------------------------------------
# 本地开发入口（Vercel 部署时不会执行到这里）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # debug 模式仅在本地开发开启；生产环境务必使用生产 WSGI 服务器（如 gunicorn）
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
