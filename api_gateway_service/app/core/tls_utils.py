import ssl

from .config import ALLOW_INSECURE_TLS


def ssl_ctx_for(url: str):
    if url.startswith("wss://"):
        ctx = ssl.create_default_context()
        if ALLOW_INSECURE_TLS:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None
