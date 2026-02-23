import os

from app import create_app

APP = create_app()
app = APP

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(APP, host="0.0.0.0", port=int(os.getenv("PORT", "8900")))