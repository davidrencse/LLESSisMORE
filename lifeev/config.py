"""Local-first configuration. Keys are never embedded in source."""
import os
from pathlib import Path
from cryptography.fernet import Fernet


def settings():
    directory = Path(os.getenv("LIFEEV_DATA_DIR", "data")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    key = os.getenv("LIFEEV_ENCRYPTION_KEY")
    if key:
        key = key.encode()
    else:
        key_path = directory / ".key"
        if not key_path.exists():
            # Exclusive creation avoids replacing a concurrent instance's key.
            try:
                with key_path.open("xb") as handle:
                    handle.write(Fernet.generate_key())
                key_path.chmod(0o600)
            except FileExistsError:
                pass
        key = key_path.read_bytes().strip()
    Fernet(key)
    return directory, key
