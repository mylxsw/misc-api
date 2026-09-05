import unittest
from unittest.mock import patch

from lib.wechat import WeChatDraftAPIError
from server import app


class WeChatDraftAPITest(unittest.TestCase):
    @patch("server.get_access_token", return_value="token")
    @patch("server.list_drafts")
    def test_list_drafts_uses_defaults(self, list_drafts, _token):
        list_drafts.return_value = {"total_count": 0, "item_count": 0, "item": []}

        with app.test_client() as client:
            response = client.get(
                "/v1/wechat/drafts",
                headers={"X-WeChat-AppId": "appid", "X-WeChat-AppSecret": "secret"},
            )

        self.assertEqual(response.status_code, 200)
        list_drafts.assert_called_once_with("token", 0, 10, 0)

    def test_list_drafts_validates_count(self):
        with app.test_client() as client:
            response = client.get("/v1/wechat/drafts?count=21")

        self.assertEqual(response.status_code, 400)
        self.assertIn("count", response.get_json()["error"])

    @patch("server.get_access_token", return_value="token")
    @patch("server.get_draft")
    def test_get_draft_returns_detail(self, get_draft, _token):
        get_draft.return_value = {"news_item": [{"title": "标题"}]}

        with app.test_client() as client:
            response = client.get(
                "/v1/wechat/drafts/media-1",
                headers={"X-WeChat-AppId": "appid", "X-WeChat-AppSecret": "secret"},
            )

        self.assertEqual(response.status_code, 200)
        get_draft.assert_called_once_with("token", "media-1")

    def test_update_requires_article(self):
        with app.test_client() as client:
            response = client.put("/v1/wechat/drafts/media-1", json={"index": 0})

        self.assertEqual(response.status_code, 400)
        self.assertIn("article", response.get_json()["error"])

    @patch("server.get_access_token", return_value="token")
    @patch("server.update_draft")
    def test_update_draft(self, update_draft, _token):
        article = {
            "title": "标题",
            "content": "<p>正文</p>",
            "thumb_media_id": "thumb-1",
        }

        with app.test_client() as client:
            response = client.put(
                "/v1/wechat/drafts/media-1",
                json={"appid": "appid", "secret": "secret", "index": 0, "article": article},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["updated"], True)
        update_draft.assert_called_once_with("token", "media-1", 0, article)

    @patch("server.upload_thumb_bytes", return_value="new-thumb")
    @patch("server.load_image_bytes", return_value=(b"image", "cover.jpg"))
    @patch("server.get_access_token", return_value="token")
    @patch("server.update_draft")
    def test_update_reuploads_request_thumb_url(
        self, update_draft, _token, load_image, upload_thumb
    ):
        article = {
            "title": "标题",
            "content": "<p>正文</p>",
            "thumb_media_id": "",
            "thumb_url": "https://example.com/cover.jpg",
            "url": "https://example.com/read-only",
        }

        with app.test_client() as client:
            response = client.put(
                "/v1/wechat/drafts/media-1",
                json={"appid": "appid", "secret": "secret", "article": article},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "media_id": "media-1",
                "index": 0,
                "updated": True,
                "cover_reuploaded": True,
                "thumb_media_id": "new-thumb",
            },
        )
        load_image.assert_called_once_with("https://example.com/cover.jpg")
        upload_thumb.assert_called_once_with("token", b"image", "cover.jpg")
        update_draft.assert_called_once_with(
            "token",
            "media-1",
            0,
            {
                "title": "标题",
                "content": "<p>正文</p>",
                "thumb_media_id": "new-thumb",
            },
        )

    @patch("server.upload_thumb_bytes", return_value="new-thumb")
    @patch("server.load_image_bytes", return_value=(b"image", "cover.png"))
    @patch("server.get_draft")
    @patch("server.get_access_token", return_value="token")
    @patch("server.update_draft")
    def test_update_recovers_thumb_url_from_existing_draft(
        self, update_draft, _token, get_draft, load_image, upload_thumb
    ):
        get_draft.return_value = {
            "news_item": [{"thumb_url": "https://example.com/existing.png"}]
        }
        article = {"title": "标题", "content": "<p>正文</p>"}

        with app.test_client() as client:
            response = client.put(
                "/v1/wechat/drafts/media-1",
                json={"appid": "appid", "secret": "secret", "article": article},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cover_reuploaded"])
        get_draft.assert_called_once_with("token", "media-1")
        load_image.assert_called_once_with("https://example.com/existing.png")
        upload_thumb.assert_called_once_with("token", b"image", "cover.png")

    @patch("server.get_draft", return_value={"news_item": [{}]})
    @patch("server.get_access_token", return_value="token")
    def test_update_rejects_news_without_available_cover(self, _token, _get_draft):
        with app.test_client() as client:
            response = client.put(
                "/v1/wechat/drafts/media-1",
                json={
                    "appid": "appid",
                    "secret": "secret",
                    "article": {"title": "标题", "content": "<p>正文</p>"},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no cover image", response.get_json()["error"])

    @patch("server.get_access_token", return_value="token")
    @patch("server.delete_draft")
    def test_delete_draft(self, delete_draft, _token):
        with app.test_client() as client:
            response = client.delete(
                "/v1/wechat/drafts/media-1",
                headers={"X-WeChat-AppId": "appid", "X-WeChat-AppSecret": "secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"media_id": "media-1", "deleted": True})
        delete_draft.assert_called_once_with("token", "media-1")

    @patch("server.get_access_token", return_value="token")
    @patch("server.get_draft")
    def test_invalid_media_id_maps_to_404(self, get_draft, _token):
        get_draft.side_effect = WeChatDraftAPIError("get", 40007, "invalid media_id")

        with app.test_client() as client:
            response = client.get(
                "/v1/wechat/drafts/missing",
                headers={"X-WeChat-AppId": "appid", "X-WeChat-AppSecret": "secret"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["wechat_errcode"], 40007)
