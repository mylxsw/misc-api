import io
import json
import unittest
from unittest.mock import Mock, patch

import requests
from PIL import Image

from lib.wechat.image_post import create_image_draft
from lib.wechat.publisher import WeChatDraftAPIError
from server import app


def png_bytes(color="white"):
    output = io.BytesIO()
    Image.new("RGB", (20, 30), color).save(output, format="PNG")
    return output.getvalue()


class WeChatImagePostTest(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "title": "图片标题",
            "content": "第一段\n\n第二段 #漫画",
            "images": ["https://example.com/1.png", "https://example.com/2.png"],
        }
        self.headers = {
            "X-WeChat-AppId": "test-app",
            "X-WeChat-AppSecret": "test-secret",
        }

    def post(self, payload=None):
        with app.test_client() as client:
            return client.post(
                "/v1/wechat/images/draft",
                json=self.payload if payload is None else payload,
                headers=self.headers,
            )

    def put(self, payload=None):
        with app.test_client() as client:
            return client.put(
                "/v1/wechat/images/draft/draft-1",
                json=self.payload if payload is None else payload,
                headers=self.headers,
            )

    @patch("server.update_draft")
    @patch("server.upload_thumb_bytes", side_effect=["permanent-1", "permanent-2"])
    @patch("server.load_image_bytes", return_value=(png_bytes(), "image.png"))
    @patch("server.get_draft", return_value={"news_item": [{"article_type": "newspic"}]})
    @patch("server.get_access_token", return_value="token")
    def test_picture_draft_update_replaces_ordered_images(self, token, get, load, upload, update):
        response = self.put()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["updated"])
        article = update.call_args.args[3]
        self.assertEqual(article["article_type"], "newspic")
        self.assertEqual(article["image_info"]["image_list"], [
            {"image_media_id": "permanent-1"}, {"image_media_id": "permanent-2"}
        ])

    @patch("server.update_draft")
    @patch("server.upload_thumb_bytes")
    @patch("server.load_image_bytes", side_effect=[(png_bytes(), "image.png"), (b"broken", "bad.png")])
    @patch("server.get_draft", return_value={"news_item": [{"article_type": "newspic"}]})
    @patch("server.get_access_token", return_value="token")
    def test_picture_draft_update_invalid_last_image_does_not_update(self, token, get, load, upload, update):
        response = self.put()
        self.assertEqual(response.status_code, 400)
        upload.assert_not_called()
        update.assert_not_called()

    @patch("lib.wechat.publisher.requests.post")
    @patch("server.upload_thumb_bytes", side_effect=["permanent-1", "permanent-2"])
    @patch("server.get_access_token", return_value="private-token")
    @patch("server.load_image_bytes")
    def test_native_payload_and_image_order(self, load, token, upload, post):
        load.side_effect = [(png_bytes("red"), "1.png"), (png_bytes("blue"), "2.png")]
        post.return_value = Mock(json=Mock(return_value={"media_id": "draft-1"}))

        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["media_id"], "draft-1")
        self.assertEqual(
            response.json["image_media_ids"], ["permanent-1", "permanent-2"]
        )
        self.assertEqual(
            [call.args[0] for call in load.call_args_list], self.payload["images"]
        )
        self.assertEqual(upload.call_count, 2)
        body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(
            body,
            {
                "articles": [
                    {
                        "article_type": "newspic",
                        "title": self.payload["title"],
                        "content": self.payload["content"],
                        "need_open_comment": 0,
                        "only_fans_can_comment": 0,
                        "image_info": {
                            "image_list": [
                                {"image_media_id": "permanent-1"},
                                {"image_media_id": "permanent-2"},
                            ]
                        },
                    }
                ]
            },
        )
        self.assertNotIn("thumb_media_id", body["articles"][0])
        token.assert_called_once_with("test-app", "test-secret")

    @patch("server.load_image_bytes")
    @patch("server.get_access_token")
    def test_invalid_requests_fail_before_network(self, token, load):
        invalid = [
            {"title": ""},
            {"title": "长" * 33},
            {"title": 1},
            {"content": "中" * 683},
            {"content": "<p>HTML</p>"},
            {"images": []},
            {"images": ["https://example.com/x"] * 21},
            {"images": ["data:image/png;base64,abc"]},
            {"images": ["/tmp/private.png"]},
            {"images": [4]},
            {"images": ["https://name:secret@example.com/x"]},
            {"images": ["https://example.com/x", "https://example.com/x"]},
            {"need_open_comment": True},
            {"only_fans_can_comment": 2},
        ]
        for override in invalid:
            with self.subTest(override=override):
                response = self.post({**self.payload, **override})
                self.assertEqual(response.status_code, 400)
        token.assert_not_called()
        load.assert_not_called()

    @patch("server.upload_thumb_bytes")
    @patch("server.get_access_token")
    @patch("server.load_image_bytes")
    def test_invalid_last_image_prevents_all_uploads(self, load, token, upload):
        load.side_effect = [(png_bytes(), "1.png"), (b"broken", "2.png")]
        response = self.post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["image_index"], 1)
        upload.assert_not_called()
        token.assert_not_called()

    @patch("lib.wechat.image_post.create_image_draft")
    @patch(
        "server.upload_thumb_bytes",
        side_effect=["permanent-1", requests.Timeout("secret")],
    )
    @patch("server.get_access_token", return_value="token")
    @patch("server.load_image_bytes", return_value=(png_bytes(), "image.png"))
    def test_upload_failure_never_creates_partial_draft(
        self, load, token, upload, create
    ):
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["stage"], "upload")
        self.assertEqual(response.json["image_media_ids"], ["permanent-1"])
        self.assertNotIn("secret", response.get_data(as_text=True))
        create.assert_not_called()

    @patch(
        "lib.wechat.publisher.requests.post",
        side_effect=requests.Timeout("private-token"),
    )
    @patch("server.upload_thumb_bytes", side_effect=["p1", "p2"])
    @patch("server.get_access_token", return_value="token")
    @patch("server.load_image_bytes", return_value=(png_bytes(), "image.png"))
    def test_uncertain_creation_is_not_retried_or_exposed(
        self, load, token, upload, post
    ):
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["outcome"], "uncertain")
        self.assertNotIn("private-token", response.get_data(as_text=True))
        post.assert_called_once()

    @patch("lib.wechat.publisher.requests.post")
    def test_empty_media_id_is_not_success(self, post):
        for value in (None, "", 42):
            post.return_value = Mock(json=Mock(return_value={"media_id": value}))
            with self.subTest(value=value), self.assertRaises(WeChatDraftAPIError):
                create_image_draft("token", "标题", "正文", ["permanent"])

    @patch("lib.wechat.publisher.requests.post")
    def test_comment_options_are_forwarded(self, post):
        post.return_value = Mock(json=Mock(return_value={"media_id": "draft"}))
        create_image_draft("token", "标题", "正文", ["permanent"], 1, 1)
        article = json.loads(post.call_args.kwargs["data"])["articles"][0]
        self.assertEqual(article["need_open_comment"], 1)
        self.assertEqual(article["only_fans_can_comment"], 1)


if __name__ == "__main__":
    unittest.main()
