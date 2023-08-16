import os
from argparse import Namespace

import uvicorn

from dstack import version
from dstack._internal.cli.commands import BasicCommand


class StartCommand(BasicCommand):
    NAME = "start"
    DESCRIPTION = "Start a server"

    def __init__(self, parser):
        super(StartCommand, self).__init__(parser)

    def _command(self, args: Namespace):
        os.environ["DSTACK_SERVER_HOST"] = args.host
        os.environ["DSTACK_SERVER_PORT"] = str(args.port)
        os.environ["DSTACK_SERVER_LOG_LEVEL"] = args.log_level
        if args.token:
            os.environ["DSTACK_SERVER_ADMIN_TOKEN"] = args.token
        uvicorn_log_level = os.getenv("DSTACK_SERVER_UVICORN_LOG_LEVEL", "error")
        uvicorn.run(
            "dstack._internal.hub.main:app",
            host=args.host,
            port=args.port,
            reload=version.__version__ is None,
            log_level=uvicorn_log_level,
        )

    def register(self):
        self._parser.add_argument(
            "--host",
            type=str,
            help="Bind socket to this host. Defaults to 127.0.0.1",
            default=os.getenv("DSTACK_SERVER_HOST", "127.0.0.1"),
        )
        self._parser.add_argument(
            "-p",
            "--port",
            type=int,
            help="Bind socket to this port. Defaults to 3000.",
            default=os.getenv("DSTACK_SERVER_PORT", 3000),
        )
        self._parser.add_argument(
            "-l",
            "--log-level",
            type=str,
            help="Logging level for hub. Defaults to ERROR.",
            default=os.getenv("DSTACK_SERVER_LOG_LEVEL", "ERROR"),
        )
        self._parser.add_argument("--token", type=str, help="The admin user token")
