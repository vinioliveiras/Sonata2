"""File access for the Assistant: Claude may list, read and create files,
only inside the folders you chose (Settings), and only after you allow each
action (the window asks; see window.py).

    TOOLS                              # the tool definitions sent to the API
    check(name, args, folders)         # -> Action (what will happen) or raises Denied
    run(action)                        # -> (text result, is_error)

Paths are resolved (symlinks, "..") before the folder check, so nothing
outside the chosen folders can be reached."""
import os

MAX_READ = 256 * 1024          # bytes read from one file
MAX_LIST = 500                 # entries listed from one folder
MAX_WRITE = 2 * 1024 * 1024    # bytes written to one file

TOOLS = [
    {"name": "list_folder",
     "description": "List the files and folders inside a folder the user allowed. Returns one entry per line: "
                    "name, then '/' for folders or the size in bytes for files.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute path of the folder."}}, "required": ["path"]}},
    {"name": "read_file",
     "description": "Read a text file inside a folder the user allowed (up to 256 KB).",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute path of the file."}}, "required": ["path"]}},
    {"name": "create_file",
     "description": "Create a text file inside a folder the user allowed (missing parent folders are created). "
                    "Fails if the file exists, unless overwrite is true.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute path of the new file."},
         "content": {"type": "string", "description": "The file's text."},
         "overwrite": {"type": "boolean", "description": "Replace an existing file."}},
         "required": ["path", "content"]}},
]
NAMES = {t["name"] for t in TOOLS}


class Denied(Exception):
    """The action can't happen (outside the folders, bad input...)."""


class Action:
    def __init__(self, name: str, path: str, args: dict, exists: bool = False):
        self.name, self.path, self.args, self.exists = name, path, args, exists

    @property
    def verb(self) -> str:
        if self.name == "list_folder":
            return "see what’s in"
        if self.name == "read_file":
            return "read"
        return "replace" if self.exists else "create"

    def summary(self) -> str:
        """Past-tense line for the chat ("Read notes.txt")."""
        base = os.path.basename(self.path.rstrip(os.sep)) or self.path
        return {"list_folder": f"Looked in {base}", "read_file": f"Read {base}"}.get(
            self.name, f"{'Replaced' if self.exists else 'Created'} {base}")


def system_prompt(folders) -> str:
    if not folders:
        return ""
    lines = "\n".join(f"- {f}" for f in folders)
    return ("You can list, read and create files with your tools, only inside these folders the user chose:\n"
            f"{lines}\nAlways use absolute paths. The user is asked to allow every action, and may refuse.")


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def inside(path: str, folders) -> bool:
    for f in folders:
        root = _real(f)
        try:
            if os.path.commonpath([root, path]) == root:
                return True
        except ValueError:
            continue
    return False


def check(name: str, args, folders) -> Action:
    if name not in NAMES:
        raise Denied(f"Unknown tool: {name}")
    if not isinstance(args, dict) or not isinstance(args.get("path"), str) or not args["path"].strip():
        raise Denied("A path is needed.")
    raw = args["path"].strip()
    if not os.path.isabs(os.path.expanduser(raw)):
        raise Denied("The path must be absolute.")
    path = _real(raw)
    if not inside(path, folders):
        raise Denied("That path is outside the folders the user allowed.")
    if name == "list_folder" and not os.path.isdir(path):
        raise Denied("No such folder.")
    if name == "read_file" and not os.path.isfile(path):
        raise Denied("No such file.")
    exists = False
    if name == "create_file":
        if not isinstance(args.get("content"), str):
            raise Denied("The file's content is needed.")
        if len(args["content"].encode("utf-8", "surrogatepass")) > MAX_WRITE:
            raise Denied("The content is too large.")
        if os.path.isdir(path):
            raise Denied("A folder has that name.")
        exists = os.path.exists(path)
        if exists and not args.get("overwrite"):
            raise Denied("The file already exists (set overwrite to replace it).")
    return Action(name, path, args, exists)


def run(action: Action):
    """Do it (after the user allowed it): (result text, is_error)."""
    try:
        if action.name == "list_folder":
            entries = []
            with os.scandir(action.path) as it:
                for e in sorted(it, key=lambda e: e.name.lower()):
                    if len(entries) >= MAX_LIST:
                        entries.append(f"… (more than {MAX_LIST} entries)")
                        break
                    try:
                        entries.append(e.name + "/" if e.is_dir() else f"{e.name}\t{e.stat().st_size}")
                    except OSError:
                        entries.append(e.name)
            return "\n".join(entries) or "(empty folder)", False
        if action.name == "read_file":
            with open(action.path, "rb") as f:
                data = f.read(MAX_READ + 1)
            if b"\0" in data[:8192]:
                return "This is not a text file.", True
            text = data[:MAX_READ].decode("utf-8", errors="replace")
            if len(data) > MAX_READ:
                text += f"\n… (cut at {MAX_READ // 1024} KB)"
            return text, False
        os.makedirs(os.path.dirname(action.path), exist_ok=True)
        tmp = action.path + ".sonata-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(action.args["content"])
        os.replace(tmp, action.path)
        return f"{'Replaced' if action.exists else 'Created'} {action.path}", False
    except OSError as e:
        return f"{e.strerror or e}", True
