"""Command line: ``carwatch serve | token | info``."""

from __future__ import annotations

import argparse
import logging

from carwatch.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="carwatch", description="Car Watch local service")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the API and UI (default)")
    sub.add_parser("token", help="print the token the extension needs (creates it on first use)")
    sub.add_parser("info", help="print where the database and token live")
    args = parser.parse_args()
    settings = Settings()

    if args.command == "token":
        print(settings.resolved_token().get_secret_value())
        return
    if args.command == "info":
        print(f"UI:       http://{settings.host}:{settings.port}/")
        print(f"Database: {settings.db_path}")
        print(f"Token:    {settings.token_file}")
        return

    import uvicorn

    from carwatch.app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
