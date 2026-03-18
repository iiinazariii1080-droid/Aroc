import ssl

from .config import settings


def ssl_ctx_for(url: str):
    if url.startswith("wss://"):
        ctx = ssl.create_default_context()
        if settings.allow_insecure_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None
