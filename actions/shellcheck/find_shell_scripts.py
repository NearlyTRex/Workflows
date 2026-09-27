"""List the tracked shell scripts, NUL-separated, for shellcheck.

A script counts by its .sh or .bash extension, or by a sh, bash, dash or ksh
shebang, so one without an extension (a git hook, an entrypoint) is still
checked. zsh is left out: shellcheck doesn't read it. Only the first line of
each file is read, which keeps this fast in a repository with tens of
thousands of files.
"""

import re
import subprocess
import sys

EXTENSIONS = (".sh", ".bash")
SHEBANG = re.compile(rb"^#!\S*/(env\s+)?(ba|da|k)?sh(\s|$)")


def is_shell_script(path):
    if path.endswith(EXTENSIONS):
        return True
    try:
        with open(path, "rb") as f:
            return bool(SHEBANG.match(f.readline(256)))
    except OSError:  # a submodule, a dangling symlink, a directory
        return False


def main():
    paths = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True,
                           text=True).stdout.split("\0")
    scripts = sorted({p for p in paths if p and is_shell_script(p)})
    sys.stdout.write("".join(f"{p}\0" for p in scripts))
    print(f"{len(scripts)} shell scripts found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
