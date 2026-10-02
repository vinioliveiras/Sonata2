"""The Markdown answers come in, as blocks the chat shows with labels.

    blocks(text)  -> [(kind, text)]   kind: p, h1, h2, h3, li, ol, quote, code, hr
    inline(text)  -> Pango markup (bold, italic, `code`, ~~strike~~, links)

Lists keep their marker in the text ("• item", "2. item"); a code block's
text is raw (shown monospaced, never parsed). An unclosed ``` (still
streaming) is a code block up to the end."""
import re

from gi.repository import GLib

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")

_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")


def blocks(text: str) -> list:
    out, para = [], []

    def end_para():
        if para:
            out.append(("p", " ".join(s.strip() for s in para)))
            para.clear()

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            end_para()
            fence = _FENCE.match(line).group(1)
            lang = line.strip()[3:].strip()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            out.append(("code", "\n".join(code), lang) if lang else ("code", "\n".join(code)))
            i += 1
            continue
        m = _HEAD.match(line)
        if m:
            end_para()
            out.append(("h%d" % min(len(m.group(1)), 3), m.group(2).strip().rstrip("#").strip()))
        elif _RULE.match(line):
            end_para()
            out.append(("hr", ""))
        elif _BULLET.match(line):
            end_para()
            ind, body = _BULLET.match(line).groups()
            out.append(("li", "    " * (len(ind.expandtabs(4)) // 2) + "•  " + body))
        elif _NUMBER.match(line):
            end_para()
            ind, n, body = _NUMBER.match(line).groups()
            out.append(("ol", "    " * (len(ind.expandtabs(4)) // 2) + f"{n}.  " + body))
        elif line.startswith(">"):
            end_para()
            body = line.lstrip(">").strip()
            if out and out[-1][0] == "quote":
                out[-1] = ("quote", out[-1][1] + "\n" + body)
            else:
                out.append(("quote", body))
        elif not line.strip():
            end_para()
        elif out and out[-1][0] in ("li", "ol") and not para and line.startswith((" ", "\t")):
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())      # a wrapped list item
        else:
            para.append(line)
        i += 1
    end_para()
    return out


def inline(text: str) -> str:
    """Pango markup for one block of Markdown text (escaped first)."""
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return f"\ue000{len(codes) - 1}\ue001"   # private-use marks (a NUL would cut the C string)
    text = _CODE.sub(keep, text)                     # code spans are never formatted
    s = GLib.markup_escape_text(text)
    s = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = _BOLD.sub(r"<b>\2</b>", s)
    s = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    s = _STRIKE.sub(r"<s>\1</s>", s)
    return re.sub("\ue000(\\d+)\ue001",
                  lambda m: f"<tt>{GLib.markup_escape_text(codes[int(m.group(1))])}</tt>", s)
