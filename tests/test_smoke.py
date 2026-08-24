import json
import os
import unittest
from unittest.mock import patch

# 先切换到项目根目录，确保能 import app
import sys
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app import app, extract_image_url


class MockResponse:
    """用于模拟 requests.post 返回对象的轻量包装。"""

    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("No JSON")
        return self._json


class SmokeTests(unittest.TestCase):
    """后端接口冒烟测试集合。"""

    def setUp(self):
        # 每次测试都创建一个新的测试客户端，避免状态污染
        self.client = app.test_client()
        # 清空密钥相关环境变量：本机 .env 中的真实 Key 会被 load_dotenv 注入，
        # 测试必须从「干净状态」出发，需要 Key 的用例自行显式设置
        self._clear_env()

    def tearDown(self):
        # 清理环境变量，避免影响后续测试
        self._clear_env()

    @staticmethod
    def _clear_env():
        for key in ("IMAGE_API_KEY", "IMAGE_API_PROVIDER", "IMAGE_API_MODEL",
                    "ARK_API_KEY", "DEEPSEEK_API_KEY"):
            os.environ.pop(key, None)

    def test_index_route(self):
        """访问根路径应返回 index.html 入口页面。"""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("AI 创意工坊".encode("utf-8"), resp.data)
        resp.close()

    def test_manifest_route(self):
        """PWA manifest 文件应正确返回。"""
        resp = self.client.get("/manifest.json")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["name"], "AI 创意工坊")
        resp.close()

    def test_service_worker_route(self):
        """Service Worker 脚本应以 JavaScript MIME 类型返回。"""
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/javascript")
        resp.close()

    def test_icons_route(self):
        """图标路由应返回真实图标文件。"""
        resp = self.client.get("/icons/icon-192.png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "image/png")
        resp.close()

    def test_health_endpoint(self):
        """健康检查应返回成功，并显示 key 未配置与默认 provider（豆包）。"""
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["status"], "ok")
        self.assertFalse(data["data"]["api_key_configured"])
        self.assertEqual(data["data"]["provider"], "doubao")
        self.assertEqual(data["data"]["model"], "doubao-seedream-5-0-pro-260628")

    def test_generate_without_api_key(self):
        """未配置 API Key 时，应提示服务端未配置密钥。"""
        resp = self.client.post(
            "/api/generate",
            json={"prompt": "test prompt"},
        )
        self.assertEqual(resp.status_code, 500)
        data = json.loads(resp.data)
        self.assertFalse(data["success"])
        self.assertIn("未配置 IMAGE_API_KEY", data["error"]["message"])

    def test_generate_empty_prompt(self):
        """空提示词应返回 400 校验错误。"""
        resp = self.client.post("/api/generate", json={"prompt": "   "})
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("请输入图像描述", data["error"]["message"])

    @patch("app.requests.post")
    def test_generate_success_url(self, mock_post):
        """模拟上游返回图片 URL，应正确提取并返回成功。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(
            200, {"data": [{"url": "https://example.com/image.png"}]}
        )

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["image_url"], "https://example.com/image.png")
        self.assertEqual(data["data"]["prompt"], "一只猫")

    @patch("app.requests.post")
    def test_generate_doubao_payload(self, mock_post):
        """默认 provider 为豆包，转发请求体应包含 model / response_format / watermark，
        且不应携带豆包不支持的 n 参数。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(
            200, {"data": [{"url": "https://example.com/image.png"}]}
        )

        resp = self.client.post("/api/generate", json={"prompt": "一只猫", "n": 3})
        self.assertEqual(resp.status_code, 200)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "doubao-seedream-5-0-pro-260628")
        self.assertEqual(payload["prompt"], "一只猫")
        self.assertEqual(payload["response_format"], "url")
        self.assertFalse(payload["watermark"])
        self.assertNotIn("n", payload)
        # 上游端点应为火山方舟
        url = mock_post.call_args.args[0]
        self.assertIn("ark.cn-beijing.volces.com", url)

    @patch("app.requests.post")
    def test_generate_ark_api_key_alias(self, mock_post):
        """使用火山方舟官方命名 ARK_API_KEY 时也应能通过密钥检查。"""
        os.environ["ARK_API_KEY"] = "fake-ark-key"
        mock_post.return_value = MockResponse(
            200, {"data": [{"url": "https://example.com/image.png"}]}
        )

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 200)
        auth_header = mock_post.call_args.kwargs["headers"]["Authorization"]
        self.assertEqual(auth_header, "Bearer fake-ark-key")

    @patch("app.requests.post")
    def test_generate_model_not_open(self, mock_post):
        """上游返回 ModelNotOpen（豆包模型未开通）时应给出可操作的中文提示。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(
            404,
            {
                "error": {
                    "code": "ModelNotOpen",
                    "message": "Your account has not activated the model",
                }
            },
        )

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 502)
        data = json.loads(resp.data)
        self.assertIn("模型未开通", data["error"]["message"])
        self.assertIn("火山方舟控制台", data["error"]["message"])

    @patch("app.requests.post")
    def test_generate_success_b64(self, mock_post):
        """模拟上游返回 base64 图片数据，应包装为 data URL。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(
            200, {"data": [{"b64_json": "iVBORw0KGgoAAAANS"}]}
        )

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["success"])
        self.assertTrue(data["data"]["image_url"].startswith("data:image/png;base64,"))

    @patch("app.requests.post")
    def test_generate_unauthorized(self, mock_post):
        """上游返回 401 时，应转换为 API Key 无效的中文提示。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(
            401,
            {"error": {"message": "Invalid API key"}},
        )

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.data)
        self.assertIn("API Key 无效", data["error"]["message"])

    @patch("app.requests.post")
    def test_generate_rate_limit(self, mock_post):
        """上游返回 429 时，应提示请求过于频繁或额度不足。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(429)

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 429)
        data = json.loads(resp.data)
        self.assertIn("请求过于频繁", data["error"]["message"])

    @patch("app.requests.post")
    def test_generate_server_error(self, mock_post):
        """上游返回 500 时，应提示图像服务繁忙。"""
        os.environ["IMAGE_API_KEY"] = "fake-key"
        mock_post.return_value = MockResponse(500)

        resp = self.client.post("/api/generate", json={"prompt": "一只猫"})
        self.assertEqual(resp.status_code, 502)
        data = json.loads(resp.data)
        self.assertIn("繁忙", data["error"]["message"])

    def test_extract_image_url_various_formats(self):
        """验证多种上游响应结构都能正确提取图片地址。"""
        # OpenAI URL 风格
        self.assertEqual(
            extract_image_url({"data": [{"url": "https://a.com/1.png"}]}),
            "https://a.com/1.png",
        )
        # 简化数组风格
        self.assertEqual(
            extract_image_url({"images": ["https://a.com/2.png"]}),
            "https://a.com/2.png",
        )
        # 扁平字段
        self.assertEqual(
            extract_image_url({"image_url": "https://a.com/3.png"}),
            "https://a.com/3.png",
        )
        # 无法识别时返回 None
        self.assertIsNone(extract_image_url({"unexpected": "value"}))


if __name__ == "__main__":
    unittest.main()
