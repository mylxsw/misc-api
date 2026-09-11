import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.wechat import WeChatConverter, list_themes, load_theme
from lib.wechat.cli import build_preview_document, main


class WeChatCliTest(unittest.TestCase):
    def test_build_preview_document_contains_copyable_article_only(self):
        document = build_preview_document(
            '<p style="color: red">正文</p>',
            title='<标题>',
            theme_name="sspai",
        )

        self.assertIn("复制到公众号", document)
        self.assertIn('id="wechat-content"', document)
        self.assertIn('<p style="color: red">正文</p>', document)
        self.assertIn("ClipboardItem", document)
        self.assertIn('id="theme-button"', document)
        self.assertIn("暗色预览", document)
        self.assertIn("data-preview-theme", document)
        self.assertIn("data-darkmode-color", document)
        self.assertIn("copyableHtml()", document)
        self.assertIn("&lt;标题&gt;", document)
        self.assertNotIn("<title><标题></title>", document)

    def test_main_generates_html_next_to_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            markdown_path = Path(directory) / "article.md"
            markdown_path.write_text("# 测试标题\n\n这是一段 **正文**。", encoding="utf-8")

            self.assertEqual(main([str(markdown_path), "--theme", "sspai"]), 0)

            output_path = markdown_path.with_suffix(".html")
            document = output_path.read_text(encoding="utf-8")
            self.assertIn("<title>测试标题</title>", document)
            self.assertIn("<strong", document)
            self.assertIn("主题：sspai", document)

    def test_spring_fresh_theme_is_available_and_inlines_key_styles(self):
        self.assertIn("spring-fresh", list_themes())

        result = WeChatConverter(theme=load_theme("spring-fresh")).convert(
            "## 胶囊标题\n\n### 小标题\n\n正文含有 **重点**。\n\n> 一段引用。\n\n:::reference\n这是一段参考说明。\n:::\n\n| 名称 | 值 |\n| --- | --- |\n| 春天 | 清新 |"
        )

        self.assertIn("background: #6b9b7a", result.html)
        self.assertIn("color: #222", result.html)
        self.assertIn('data-darkmode-color="#e6e6e6"', result.html)
        self.assertIn("border-radius: 999px", result.html)
        self.assertIn("border-left: 5px solid #6b9b7a", result.html)
        self.assertIn("background: #f3f7f4", result.html)
        self.assertIn("border-radius: 12px", result.html)
        self.assertIn("box-shadow: inset 0 0 16px rgba(107, 155, 122, 0.1)", result.html)
        self.assertIn("color: #526158; margin: 0", result.html)
        self.assertIn("background: #f1f6f2", result.html)
        self.assertIn("border-radius: 14px", result.html)
        self.assertIn("这是一段参考说明。", result.html)
        self.assertIn("data-darkmode-bgcolor", result.html)

    def test_line_art_theme_is_available_and_inlines_key_styles(self):
        self.assertIn("line-art", list_themes())

        theme = load_theme("line-art")
        result = WeChatConverter(theme=theme).convert(
            "# 一句话原则\n\n## 只留一句\n\n正文中的 **重点** 和 `代码`。\n\n> 这是一张线框便签。\n\n- 列表项\n\n| 名称 | 值 |\n| --- | --- |\n| 风格 | 线稿 |\n\n```text\nkeep it simple\n```"
        )

        self.assertEqual(result.title, "一句话原则")
        self.assertIn('font-family: "Kaiti SC"', result.html)
        self.assertIn("border-bottom: 3px solid currentColor", theme.base_css)
        self.assertIn("border: 2px solid currentColor", result.html)
        self.assertIn("border-bottom: 2px solid currentColor", result.html)
        self.assertIn("background: #fafafa", result.html)
        self.assertIn('data-darkmode-color="#dedede"', result.html)
        self.assertIn('data-darkmode-bgcolor="#202020"', result.html)
        self.assertRegex(result.html, r'<section[^>]+data-darkmode-color="#dedede"')
        self.assertRegex(result.html, r'<td[^>]+data-darkmode-bgcolor="transparent"')
        self.assertRegex(result.html, r'<tr[^>]+data-darkmode-bgcolor="transparent"')
        self.assertRegex(result.html, r'<code[^>]+data-darkmode-bgcolor="#242424"')

    def test_main_reports_unknown_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            markdown_path = Path(directory) / "article.md"
            markdown_path.write_text("正文", encoding="utf-8")

            with patch("sys.stderr"):
                self.assertEqual(main([str(markdown_path), "--theme", "missing"]), 2)
            self.assertFalse(markdown_path.with_suffix(".html").exists())


if __name__ == "__main__":
    unittest.main()
