
import os
import sys

from bridge import _arg, run_desktop, run_web


def main() -> None:
    if "--desktop" in sys.argv:
        run_desktop()
        return
    run_web(_arg("--host", os.environ.get("HOST", "127.0.0.1")),
            int(_arg("--port", os.environ.get("PORT", 5000))))


if __name__ == "__main__":
    main()
