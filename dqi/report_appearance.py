"""Embed the report palette so exported HTML works offline and without JavaScript."""

_DARK_PALETTE = """
  color-scheme: dark;
  --ink: #e6edf0;
  --muted: #b0bec8;
  --line: #394c56;
  --surface: #1a272e;
  --canvas: #101a20;
  --inset: #202f37;
  --track: #34464f;
  --accent: #7bd4c3;
  --link: #8ce0d0;
  --positive-bg: #1b3b32;
  --positive-ink: #a0e8c7;
  --attention-bg: #41351f;
  --attention-ink: #f5d499;
  --neutral-bg: #303f48;
  --neutral-ink: #cedbe1;
"""

REPORT_PALETTE = """
:root {
  color-scheme: light;
  --ink: #172b3a;
  --muted: #546675;
  --line: #dce4e9;
  --surface: #fff;
  --canvas: #f3f6f8;
  --inset: #f4f7f8;
  --track: #edf1f4;
  --accent: #286d78;
  --link: #176274;
  --positive-bg: #dff0e9;
  --positive-ink: #195c43;
  --attention-bg: #fff0d9;
  --attention-ink: #774b0b;
  --neutral-bg: #e7ecf0;
  --neutral-ink: #425463;
}
""" + f"""
@media screen and (prefers-color-scheme: dark) {{
  :root:not([data-theme]) {{ {_DARK_PALETTE} }}
}}
@media screen {{
  :root[data-theme="dark"] {{ {_DARK_PALETTE} }}
}}
"""


def with_report_theme(body: bytes, theme: str) -> bytes:
    """Apply an explicit workspace choice to the served copy of a generated report."""
    if theme not in ("light", "dark"):
        return body
    root = f'<html lang="en" data-theme="{theme}">'.encode("ascii")
    return body.replace(b'<html lang="en">', root, 1)
