"""Local synthetic UI fixture. Never connects to Azure."""

from app import DeskServer
from tests.test_app import FixtureService, VAULTS


if __name__ == "__main__":
    server = DeskServer(("127.0.0.1", 8766), VAULTS, FixtureService())
    print(f"Synthetic UI fixture only: {server.origin}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
