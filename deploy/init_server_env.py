"""Create server environment file once, without printing generated secrets."""
import os
from pathlib import Path
import secrets
import sys


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "/etc/dzmm/dzmm.env")
    template = Path(__file__).with_name("server.env.example").read_text(encoding="utf-8")
    content = template.replace("GENERATE_CORE_TOKEN", secrets.token_urlsafe(32)).replace(
        "GENERATE_ADMIN_TOKEN", secrets.token_urlsafe(32))
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(content)
    print("Server environment created; set the target group URL before starting the Worker.")


if __name__ == "__main__":
    main()
