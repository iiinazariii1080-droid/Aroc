import os

from app.core.logging_config import configure_logging

configure_logging()

from app import create_app  # noqa: E402 — must be after logging config

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8900")))
