import os
import time

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

DOUBAO_IMAGE_API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
OPENAI_IMAGE_API_URL = "https://api.openai.com/v1/images/generations"
DEEPSEEK_IMAGE_API_URL = "https://api.deepseek.com/v1/image/generate"

DEFAULT_IMAGE_API_PROVIDER = "doubao"
DEFAULT_IMAGE_API_MODEL = "doubao-seedream-5-0-pro-260628"

REQUEST_TIMEOUT_SECONDS = 120

MAX_PROMPT_LENGTH = 2000

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

CORS(app)

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

@app.post("/api/generate")
def generate_image():
    body = request.get_json(silent=True) or {}

    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        return build_error_response("请输入图像描述（prompt）", 400)
    if len(prompt) > MAX_PROMPT_LENGTH:
        return build_error_response(f"描述过长，请控制在 {MAX_PROMPT_LENGTH} 字以内", 400)

    size = str(body.get("size") or "1024x1024").strip()
    n = int(body.get("n") or 1)
    api_key = get_api_key()
    if api_key is None:
        # 服务端未配置密钥：提示部署者，而不是前端用户
        return build_error_response(
            "服务端未配置 IMAGE_API_KEY，请联系管理员在环境变量中设置", 500
        )
    config = get_image_api_config()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "size": size,
        **config["extra_payload"],
    }
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
        return build_error_response("图像生成超时，请稍后重试", 504)
    except requests.exceptions.ConnectionError:
        return build_error_response("无法连接图像服务，请检查网络后重试", 502)
    except requests.exceptions.RequestException as exc:
        app.logger.error("图像 API 请求异常: %s", exc)
        return build_error_response("图像服务暂时不可用，请稍后重试", 502)
    elapsed = round(time.time() - start_time, 2)

    if upstream_resp.status_code != 200:
        return handle_upstream_error(upstream_resp)

    try:
        upstream_data = upstream_resp.json()
    except ValueError:
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
    status = upstream_resp.status_code
    upstream_msg = ""
    upstream_code = ""
    try:
        upstream_error = upstream_resp.json().get("error", {})
        upstream_msg = upstream_error.get("message", "")
        upstream_code = upstream_error.get("code", "")
    except ValueError:
        pass
    if upstream_code == "ModelNotOpen" or "has not activated the model" in upstream_msg:
        return build_error_response(
            "豆包模型未开通：请到火山方舟控制台「开通管理」中开通该图像生成模型后再试",
            502,
        )
    if upstream_code == "InvalidEndpointOrModel.NotFound":
        return build_error_response(
            "模型或接入点不存在：请检查 IMAGE_API_MODEL 配置是否正确",
            502,
        )

    if status == 401 or status == 403:
        return build_error_response("API Key 无效或已过期，请联系管理员检查配置", 401)
    if status == 429:
        return build_error_response("请求过于频繁或额度不足，请稍后再试", 429)
    if status >= 500:
        return build_error_response("图像服务繁忙，请稍后重试", 502)

    app.logger.error("图像 API 返回状态码 %s: %s", status, upstream_msg)
    return build_error_response("图像生成失败，请稍后重试", 502)


def extract_image_url(data: dict) -> str | None:
    data_list = data.get("data")
    if isinstance(data_list, list) and data_list:
        first = data_list[0] if isinstance(data_list[0], dict) else {}
        url = first.get("url")
        if url:
            return str(url)
        b64 = first.get("b64_json")
        if b64:
            return data_url_from_base64(str(b64))

    images = data.get("images")
    if isinstance(images, list) and images and isinstance(images[0], str):
        return images[0]

    flat_url = data.get("image_url")
    if flat_url:
        return str(flat_url)

    return None


def data_url_from_base64(b64_text: str) -> str:
    if b64_text.startswith("data:"):
        return b64_text
    return f"data:image/png;base64,{b64_text}"

if __name__ == "__main__":
    # debug 模式仅在本地开发开启；生产环境务必使用生产 WSGI 服务器（如 gunicorn）
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
