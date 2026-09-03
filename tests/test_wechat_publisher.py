import json
import unittest
from unittest.mock import Mock, patch

import requests

from lib.wechat.publisher import (
    WeChatDraftAPIError,
    delete_draft,
    get_draft,
    list_drafts,
    update_draft,
)


class WeChatPublisherTest(unittest.TestCase):
    def _response(self, payload):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response

    @patch("lib.wechat.publisher.requests.post")
    def test_list_drafts_posts_pagination(self, post):
        post.return_value = self._response(
            {"total_count": 1, "item_count": 1, "item": []}
        )

        result = list_drafts("token", offset=2, count=5, no_content=1)

        self.assertEqual(result["total_count"], 1)
        self.assertEqual(post.call_args.args[0], "https://api.weixin.qq.com/cgi-bin/draft/batchget")
        self.assertEqual(post.call_args.kwargs["params"], {"access_token": "token"})
        self.assertEqual(
            json.loads(post.call_args.kwargs["data"]),
            {"offset": 2, "count": 5, "no_content": 1},
        )

    @patch("lib.wechat.publisher.requests.post")
    def test_get_update_and_delete_use_expected_payloads(self, post):
        post.side_effect = [
            self._response({"news_item": []}),
            self._response({"errcode": 0, "errmsg": "ok"}),
            self._response({"errcode": 0, "errmsg": "ok"}),
        ]
        article = {"title": "中文标题", "content": "<p>正文</p>"}

        get_draft("token", "media-1")
        update_draft("token", "media-1", 0, article)
        delete_draft("token", "media-1")

        self.assertEqual(json.loads(post.call_args_list[0].kwargs["data"]), {"media_id": "media-1"})
        update_body = json.loads(post.call_args_list[1].kwargs["data"])
        self.assertEqual(update_body, {"media_id": "media-1", "index": 0, "articles": article})
        self.assertIn("中文标题".encode(), post.call_args_list[1].kwargs["data"])
        self.assertEqual(json.loads(post.call_args_list[2].kwargs["data"]), {"media_id": "media-1"})

    @patch("lib.wechat.publisher.requests.post")
    def test_wechat_error_is_structured(self, post):
        post.return_value = self._response({"errcode": 40007, "errmsg": "invalid media_id"})

        with self.assertRaises(WeChatDraftAPIError) as caught:
            get_draft("token", "missing")

        self.assertEqual(caught.exception.errcode, 40007)
        self.assertEqual(caught.exception.operation, "get")

    @patch("lib.wechat.publisher.requests.post")
    def test_transport_error_is_normalized(self, post):
        post.side_effect = requests.Timeout("timed out")

        with self.assertRaises(WeChatDraftAPIError) as caught:
            list_drafts("token")

        self.assertEqual(caught.exception.errcode, "transport_error")
