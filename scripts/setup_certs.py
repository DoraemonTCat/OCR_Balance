"""Build a CA bundle that includes the machine's TLS-inspecting proxy.

Why this exists: on a machine running antivirus or corporate software that
inspects TLS (Avast, Kaspersky, Zscaler, a company proxy), every HTTPS
connection is re-signed by that product's own root certificate. Python does not
use the Windows certificate store, so ``requests`` - and therefore PaddleOCR's
model download - fails with::

    SSLError: certificate verify failed: unable to get local issuer certificate

The fix is not to turn verification off. It is to tell Python to trust that root
as well, by appending it to certifi's bundle. This script does that and writes
the result to ``certs/ca-bundle.pem``.

Usage::

    .venv\\Scripts\\python.exe scripts\\setup_certs.py
    .venv\\Scripts\\python.exe scripts\\setup_certs.py --ca "C:\\path\\to\\root.pem"

Then set this in ``.env`` (the setting is already there, commented out)::

    REQUESTS_CA_BUNDLE=certs/ca-bundle.pem

``config/settings/base.py`` resolves that path against the project root and
exports it to ``SSL_CERT_FILE`` too, so both ``requests`` and ``ssl`` use it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT = BASE_DIR / "certs" / "ca-bundle.pem"

#: Roots that TLS-inspecting products on Windows install, in their usual
#: locations. Anything found is appended; a missing one is not an error.
KNOWN_ROOTS = (
    Path(r"C:\ProgramData\Avast Software\Avast\wscert.pem"),
    Path(r"C:\ProgramData\AVG\Antivirus\wscert.pem"),
    Path(r"C:\ProgramData\Kaspersky Lab\AVP21.3\Data\Cert\fake_ca.cer"),
    Path(r"C:\ProgramData\Zscaler\ZscalerRootCertificate.crt"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ca",
        action="append",
        default=[],
        type=Path,
        help="extra PEM root to trust; repeatable",
    )
    args = parser.parse_args(argv)

    try:
        import certifi
    except ImportError:
        print("certifi is not installed; activate the project venv first", file=sys.stderr)
        return 1

    extras = [path for path in (*args.ca, *KNOWN_ROOTS) if path.is_file()]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("wb") as bundle:
        bundle.write(Path(certifi.where()).read_bytes())
        for path in extras:
            bundle.write(b"\n")
            bundle.write(path.read_bytes())

    print(f"wrote {OUTPUT}")
    print(f"  certifi: {certifi.where()}")
    if extras:
        for path in extras:
            print(f"  added:   {path}")
    else:
        print("  no TLS-inspection root found; the bundle is plain certifi.")
        print("  If downloads still fail, pass the root with --ca.")
    print()
    print("Now put this in .env:")
    print(f"  REQUESTS_CA_BUNDLE={OUTPUT.relative_to(BASE_DIR).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
