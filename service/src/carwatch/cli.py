"""Command line: ``carwatch install | uninstall | info | ui | revalue``."""

from __future__ import annotations

import argparse
import logging
import sys

from carwatch.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="carwatch", description="Car Watch: local store of polovniautomobili.com ads")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="register the native messaging host with Chrome/Chromium (Linux)")
    sub.add_parser("uninstall", help="remove the host registration")
    sub.add_parser("info", help="print where the database and the host registration live")
    sub.add_parser("ui", help="run the web UI on 127.0.0.1 until you stop it")
    sub.add_parser("revalue", help="recompute the fair prices now")
    args = parser.parse_args()
    settings = Settings()

    if args.command in {"install", "uninstall", "info"}:
        from carwatch import install

        if not sys.platform.startswith("linux"):
            sys.exit("carwatch install za sada radi samo na Linux-u")
        if args.command == "install":
            for target in install.install():
                print(f"{target.browser}: {target.path}")
            print(f"Extension ID: {install.EXTENSION_ID} (učitaj extension/.output/chrome-mv3 ponovo)")
        elif args.command == "uninstall":
            removed = install.uninstall()
            print("\n".join(f"Uklonjeno: {t.path}" for t in removed) or "Nije bilo ničega za uklanjanje")
        else:
            print(f"Database:  {settings.db_path}")
            print(f"Host:      {install.host_program()}")
            found = install.installed()
            for target in found:
                print(f"Manifest:  {target.path} ({target.browser})")
            if not found:
                print("Manifest:  nije instaliran (uv run carwatch install)")
            print(f"Extension: {install.EXTENSION_ID}")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "revalue":
        from carwatch.jobs import revalue

        if not revalue(settings):
            print("Procena već radi")
        return

    import uvicorn

    from carwatch.app import create_app

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
