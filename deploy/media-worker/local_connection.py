"""Preserve system proxies for external APIs while bypassing loopback SSH tunnels."""

import os
import urllib.request


def configure_local_connection():
    # On Windows, setting only NO_PROXY can suppress discovery of registry proxies.
    # Copy the effective proxy routes into this process before adding the bypass.
    effective = urllib.request.getproxies()
    for scheme in ("http", "https", "all"):
        if effective.get(scheme):
            os.environ.setdefault(scheme.upper() + "_PROXY", effective[scheme])
    bypass = effective.get("no", "").replace(";", ",").split(",")
    bypass.extend(["localhost", "127.0.0.1", "::1"])
    os.environ["NO_PROXY"] = ",".join(
        dict.fromkeys(x.strip() for x in bypass if x.strip())
    )
