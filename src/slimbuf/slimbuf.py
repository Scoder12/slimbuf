from dataclasses import dataclass
from typing import List
import json
import string


INDENT = " " * 4
COMMENT = (
    "This code is automatically generated by bufcompile, editing is not recommended"
)


def indent(text, amt=1, skip_first=False):
    prefix = ""
    lines = text.split("\n")
    if skip_first:
        prefix = lines[0] + "\n"
        lines = lines[1:]
    return prefix + "\n".join([INDENT * amt + l for l in lines])


GO_START = (
    "//"
    + COMMENT
    + """

import (
    "bytes"
    "image/color"
    "fmt"
    "strings"
)

// hackishly prevent "imported but unused"
func _(){fmt.Print(strings.TrimSpace(""))}


func writeColor(buf *bytes.Buffer, c color.Color) {
    r, g, b, a := c.RGBA()
    buf.Grow(4)
    buf.WriteRune(rune(r >> 8))
    buf.WriteRune(rune(g >> 8))
    buf.WriteRune(rune(b >> 8))
    buf.WriteRune(rune(a >> 8))
}


"""
)


PARSER = """function(msg) {
    if (msg.length < 1) return
    const cmd = DATA[msg[0]]
    if (!cmd) {
        return console.error("Unrecognized func id: " + msg)
    }
    let i = 0;
    function next() {
        i++
        if (i > msg.length - 1) {
            throw new Error("Next character requested when there is none")
        }
        return msg[i]
    }
    const getString = () => {
        const len = next().charCodeAt(0)
        let chars = []
        for (let i = 0; i < len; i++) {
            chars.push(next())
        }
        return chars.join("")
    }

    const args = Array.from(cmd[0]).map(t => {
        if (t == "i") { // int
            return next().charCodeAt(0)
        } else if (t == "s") {
            return getString()
        } else if (t == "f") {
            return parseFloat(getString())
        } else {
            throw new Error(`Invalid arg type: '${t}'`)
        }
    })

    console.log("args:", args)
    try {
        cmd[1].apply(ctx, args)
    } catch (e) {
        console.error(e)
    }
}
"""


JS_START = (
    "/*"
    + COMMENT
    + """*/
function bufcompileParser(ctx) {
    const DATA = """
)

JS_END = (
    f"""
    return {indent(PARSER, amt=1, skip_first=True)}"""
    + """
}
/* End bufcompile generated code */
"""
)


@dataclass
class Arg:
    aname: str
    atype: str

    def gen_go_encode(self, fid, buf="buf"):
        if self.atype == "int":
            return f"{buf}.WriteRune(rune({self.aname}))", 1
        elif self.atype == "color.Color":
            return f"writeColor(&{buf}, {self.aname})", 0
        elif self.atype == "string":
            # extra char for length of string
            return (
                f"buf.WriteRune(rune(len([]rune({self.aname}))))\n"
                f"buf.WriteString({self.aname})",
                1,
            )
        elif self.atype == "float32":
            return (
                f'{self.aname}_str := strings.TrimRight(fmt.Sprintf("%f", {self.aname}), "0")\n'
                f"buf.WriteRune(rune(len([]rune({self.aname}_str))))\n"
                f"buf.WriteString({self.aname}_str)",
                1,
            )
        else:
            raise ValueError(f"Arg {self.aname!r} has invalid type {self.atype!r}")

    def gen_js_arg(self):
        if self.atype == "color.Color":
            return list("rgba")
        else:
            return [self.aname]

    def js_char(self):
        """Describe type in 1 character"""
        if self.atype == "int":
            return "i"
        elif self.atype == "color.Color":
            return "i" * 4
        elif self.atype == "string":
            return "s"
        elif self.atype == "float32":
            return "f"
        else:
            raise ValueError(f"Arg {self.name!r} has invalid type {self.atype!r}")


@dataclass
class Func:
    name: str
    args: List[Arg]
    js: str
    fid: str

    def gen_go(self):
        arg_go = ", ".join(f"{a.aname} {a.atype}" for a in self.args)

        body = []
        total_bsize = 1

        for a in self.args:
            c, bsize = a.gen_go_encode(self.fid)
            total_bsize += bsize
            body.append(indent(c))

        # can't use capitalize because it lowers all other chars
        go_func_name = self.name[0].upper() + self.name[1:]

        go_lines = [
            f"func {go_func_name}({arg_go}) []byte" + "{",
            INDENT + "var buf bytes.Buffer",
        ]

        go_lines.append(INDENT + f"buf.Grow({total_bsize})")
        go_lines.append(INDENT + f"buf.WriteRune('{self.fid}')")

        go_lines += body + [
            # INDENT + "hub.broadcast <- buf.Bytes()",
            INDENT + "return buf.Bytes()",
            "}",
        ]
        return "\n".join(go_lines)

    def gen_js_obj(self):
        args = [i for a in self.args for i in a.gen_js_arg()]
        func_js = "\n".join(
            [f"function ({', '.join(args)})" + " {", indent(self.js or ""), "}"]
        )

        arg_chars = "".join(a.js_char() for a in self.args)
        # if its a letter or a number don't quote it, otherwise use json.dumps to quote
        safe_fid = (
            self.fid
            if self.fid in (string.ascii_letters + string.digits)
            else json.dumps(self.fid)
        )

        lines = [
            f"{safe_fid}: " + "[",
            # INDENT + f'fid: "{self.fid}"',
            INDENT + f"{json.dumps(arg_chars)},",
            INDENT + indent(func_js, amt=1, skip_first=True),
            "]",
        ]
        return "\n".join(lines)


class FuncDef:
    def __init__(self):
        self.data = {}
        self.funcs = []
        self.next_id = ord("0")

    def parse_func(self, fname, lines):
        args = []
        js = None

        for l in lines:
            l = l.strip()
            if not l or l.startswith("#"):
                continue

            if js is not None:
                js.append(l)
            elif l.startswith("js: "):
                js = [l[4:]] if l[4:] else []
            else:
                argname, argtype = l.split(" ")
                # print(argname, argtype)
                args.append(Arg(argname, argtype))

        f = Func(name=fname, args=args, js="\n".join(js or []), fid=chr(self.next_id))
        self.next_id += 1
        return f

    def parse(self, data):
        inside_f = None
        inside_lines = []

        for l in data.split("\n"):
            l = l.strip()
            if not l:
                continue

            if inside_f:
                if l == "end":
                    self.funcs.append(self.parse_func(inside_f, inside_lines))
                    inside_f = []
                    inside_lines = []
                else:
                    inside_lines.append(l)

            if l.startswith("set"):
                _, k, v = l.split(" ")
                self.data[k] = v
            elif l.startswith("f "):
                fname = l[2:].split(" ")[0]
                # print("!", fname)
                inside_f = fname
                inside_lines = []

    def gen_go(self):
        if "gopkg" not in self.data:
            raise ValueError("Missing gopkg declaration (need: set gopkg mypkgname)")

        return (
            f"package {self.data['gopkg']}\n"
            + GO_START
            + "\n\n".join(f.gen_go() for f in self.funcs)
            + "\n"
        )

    def gen_js(self):
        funcs = (
            "{\n"
            + ", \n".join([indent(f.gen_js_obj(), 2) for f in self.funcs])
            + "\n"
            + INDENT
            + "}"
        )
        return JS_START + funcs + JS_END

    def write_from_data(self):
        if "goout" in self.data:
            with open(self.data["goout"], "w") as f:
                f.write(self.gen_go())
            print(f"Wrote go to {self.data['goout']}")
        if "jsout" in self.data:
            with open(self.data["jsout"], "w") as f:
                f.write(self.gen_js())
            print(f"Wrote js to {self.data['jsout']}")
