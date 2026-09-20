import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

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

    def test_block_code_uses_explicit_wechat_safe_breaks_and_spaces(self):
        result = WeChatConverter().convert(
            "正文中的 `inline_code`。\n\n"
            "```python\n"
            "from package import value\n"
            "if value:\n"
            "    print(value)\n"
            "```"
        )
        soup = BeautifulSoup(result.html, "html.parser")

        self.assertIsNone(soup.find("pre"))
        self.assertEqual(soup.find("code").get_text(), "inline_code")
        self.assertTrue(
            any("正文中的" in paragraph.get_text() for paragraph in soup.find_all("p"))
        )

        code_block = next(
            section
            for section in soup.find_all("section")
            if "from package" in section.get_text()
        )
        self.assertEqual(len(code_block.find_all("br")), 3)
        self.assertIn("from package import value", code_block.get_text())
        self.assertIn("\u00a0\u00a0\u00a0\u00a0print(value)", code_block.get_text())
        self.assertNotIn("white-space:", code_block.get("style", ""))
        self.assertIn("overflow-x: auto", code_block.get("style", ""))
        self.assertIn("max-width: 100%", code_block.get("style", ""))
        self.assertIn("overflow-wrap: anywhere", code_block.get("style", ""))
        self.assertEqual(code_block["data-darkmode-bgcolor"], "#2d2d2d")
        code_content = code_block.find("p", recursive=False)
        self.assertEqual(code_content["data-darkmode-color"], "#d4d4d4")
        self.assertFalse(code_content.find_all("span"))

    def test_block_code_preserves_blank_lines_tabs_and_html_characters(self):
        result = WeChatConverter().convert(
            "```\n"
            "\tif left < right:\n"
            "\n"
            "\t\tprint(\"A & B\")\n"
            "```"
        )
        soup = BeautifulSoup(result.html, "html.parser")
        code_block = soup.find("section")
        code_content = code_block.find("p", recursive=False)

        self.assertEqual(len(code_content.find_all("br")), 3)
        self.assertIn("\u00a0" * 4 + "if", code_content.get_text())
        self.assertIn("\u00a0" * 8 + 'print("A & B")', code_content.get_text())
        self.assertIn("<", code_content.get_text())
        self.assertIn("&lt;", str(code_content))
        self.assertIn("&amp;", str(code_content))
        self.assertNotIn("\n", str(code_content))
        self.assertIn("<br/><br/>", str(code_content))

    def test_raw_html_pre_block_is_also_made_wechat_safe(self):
        result = WeChatConverter().convert("<pre>line one\n  line two</pre>")
        soup = BeautifulSoup(result.html, "html.parser")

        self.assertIsNone(soup.find("pre"))
        code_block = soup.find("section")
        code_content = code_block.find("p", recursive=False)
        self.assertEqual(len(code_content.find_all("br")), 1)
        self.assertEqual(code_content.get_text(), "line one\u00a0\u00a0line two")

    def test_mixed_raw_pre_converts_newlines_inside_and_outside_code(self):
        result = WeChatConverter().convert(
            "<pre>outer one\n<code>inner one\n  inner two</code>\nouter two</pre>"
        )
        soup = BeautifulSoup(result.html, "html.parser")
        code_block = soup.find("section")

        self.assertIsNone(soup.find("pre"))
        self.assertEqual(len(code_block.find_all("br")), 3)
        self.assertNotIn("\n", str(code_block))
        self.assertIn("\u00a0\u00a0inner two", code_block.get_text())

    def test_long_code_line_keeps_scroll_and_wrap_fallbacks(self):
        long_line = "prefix " + "x" * 500
        result = WeChatConverter().convert(f"```text\n{long_line}\n```")
        soup = BeautifulSoup(result.html, "html.parser")
        code_block = soup.find("section")

        self.assertIn(long_line, code_block.get_text())
        self.assertIn("overflow-x: auto", code_block["style"])
        self.assertIn("overflow-wrap: anywhere", code_block["style"])
        self.assertIn("max-width: 100%", code_block["style"])

    def test_main_reports_unknown_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            markdown_path = Path(directory) / "article.md"
            markdown_path.write_text("正文", encoding="utf-8")

            with patch("sys.stderr"):
                self.assertEqual(main([str(markdown_path), "--theme", "missing"]), 2)
            self.assertFalse(markdown_path.with_suffix(".html").exists())


if __name__ == "__main__":
    unittest.main()
