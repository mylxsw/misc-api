"""Command-line interface for rendering Markdown as WeChat-ready HTML."""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from pathlib import Path

from .converter import WeChatConverter
from .theme import load_theme, list_themes

DEFAULT_THEME = "professional-clean"


def build_preview_document(
    body_html: str,
    *,
    title: str,
    theme_name: str,
    base_url: str | None = None,
) -> str:
    """Build a browser preview with a rich-HTML copy button."""
    escaped_title = html.escape(title or "微信公众号文章预览")
    escaped_theme = html.escape(theme_name)
    base_tag = f'    <base href="{html.escape(base_url, quote=True)}">\n' if base_url else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
{base_tag}    <title>{escaped_title}</title>
    <style>
        :root {{ color-scheme: light; }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; background: #f4f5f7; color: #1f2329; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
        .toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 20px; background: rgba(255, 255, 255, .96); border-bottom: 1px solid #e5e6eb; box-shadow: 0 2px 8px rgba(0, 0, 0, .04); }}
        .toolbar-info {{ min-width: 0; }}
        .toolbar-title {{ overflow: hidden; font-size: 14px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }}
        .toolbar-meta {{ margin-top: 2px; color: #86909c; font-size: 12px; }}
        .copy-button {{ flex: none; padding: 9px 18px; border: 0; border-radius: 7px; background: #07c160; color: #fff; cursor: pointer; font-size: 14px; font-weight: 600; }}
        .copy-button:hover {{ background: #06ad56; }}
        .copy-button:focus-visible {{ outline: 3px solid rgba(7, 193, 96, .25); outline-offset: 2px; }}
        .copy-button[data-state="success"] {{ background: #00a870; }}
        .copy-button[data-state="error"] {{ background: #f53f3f; }}
        .preview-shell {{ width: min(100% - 32px, 720px); margin: 24px auto 64px; padding: 32px 40px; background: #fff; box-shadow: 0 4px 24px rgba(0, 0, 0, .08); }}
        @media (max-width: 600px) {{ .toolbar {{ padding: 10px 12px; }} .preview-shell {{ width: 100%; margin: 0; padding: 24px 18px; box-shadow: none; }} }}
    </style>
</head>
<body>
    <header class="toolbar">
        <div class="toolbar-info">
            <div class="toolbar-title">{escaped_title}</div>
            <div class="toolbar-meta">主题：{escaped_theme}</div>
        </div>
        <button id="copy-button" class="copy-button" type="button">复制到公众号</button>
    </header>
    <main class="preview-shell">
        <section id="wechat-content">{body_html}</section>
    </main>
    <script>
        const button = document.getElementById('copy-button');
        const content = document.getElementById('wechat-content');

        function setButtonState(text, state) {{
            button.textContent = text;
            button.dataset.state = state;
            window.setTimeout(() => {{
                button.textContent = '复制到公众号';
                delete button.dataset.state;
            }}, 1800);
        }}

        function fallbackCopy() {{
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(content);
            selection.removeAllRanges();
            selection.addRange(range);
            const copied = document.execCommand('copy');
            selection.removeAllRanges();
            if (!copied) throw new Error('copy command failed');
        }}

        button.addEventListener('click', async () => {{
            try {{
                if (navigator.clipboard && window.ClipboardItem) {{
                    const rich = new Blob([content.innerHTML], {{ type: 'text/html' }});
                    const plain = new Blob([content.innerText], {{ type: 'text/plain' }});
                    await navigator.clipboard.write([new ClipboardItem({{
                        'text/html': rich,
                        'text/plain': plain,
                    }})]);
                }} else {{
                    fallbackCopy();
                }}
                setButtonState('已复制', 'success');
            }} catch (error) {{
                try {{
                    fallbackCopy();
                    setButtonState('已复制', 'success');
                }} catch (fallbackError) {{
                    console.error(fallbackError);
                    setButtonState('复制失败，请手动复制', 'error');
                }}
            }}
        }});
    </script>
</body>
</html>
"""


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wechat-markdown",
        description="将 Markdown 转换为可复制到微信公众号编辑器的 HTML 预览。",
    )
    parser.add_argument("markdown", nargs="?", type=Path, help="Markdown 文件路径")
    parser.add_argument("-t", "--theme", default=DEFAULT_THEME, help=f"主题名称（默认：{DEFAULT_THEME}）")
    parser.add_argument("-o", "--output", type=Path, help="输出 HTML 路径（默认与 Markdown 同目录、同名）")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="生成后使用默认浏览器打开")
    parser.add_argument("--list-themes", action="store_true", help="列出所有可用主题后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)

    if args.list_themes:
        for name in list_themes():
            theme = load_theme(name)
            print(f"{name}\t{theme.description}")
        return 0

    if args.markdown is None:
        print("错误：请指定 Markdown 文件，或使用 --list-themes 查看主题。", file=sys.stderr)
        return 2

    input_path = args.markdown.expanduser().resolve()
    if not input_path.is_file():
        print(f"错误：Markdown 文件不存在：{input_path}", file=sys.stderr)
        return 2

    try:
        theme = load_theme(args.theme)
    except FileNotFoundError:
        available = ", ".join(list_themes())
        print(f"错误：主题不存在：{args.theme}\n可用主题：{available}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：无法加载主题：{exc}", file=sys.stderr)
        return 2

    try:
        source = input_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"错误：无法读取 Markdown 文件：{exc}", file=sys.stderr)
        return 1

    result = WeChatConverter(theme=theme).convert(source)
    output_path = (args.output or input_path.with_suffix(".html")).expanduser().resolve()
    document = build_preview_document(
        result.html,
        title=result.title or input_path.stem,
        theme_name=args.theme,
        base_url=input_path.parent.as_uri() + "/",
    )

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(document, encoding="utf-8")
    except OSError as exc:
        print(f"错误：无法写入 HTML 文件：{exc}", file=sys.stderr)
        return 1

    print(f"已生成：{output_path}")
    if args.open_browser:
        webbrowser.open(output_path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
