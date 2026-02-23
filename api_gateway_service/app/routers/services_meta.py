from fastapi import APIRouter
from fastapi.responses import Response, FileResponse

from app.core.config import BASE_DIR

router = APIRouter(tags=["Services"])

@router.get("/favicon.ico")
async def favicon():
    fav = BASE_DIR / "static" / "favicon.ico"
    if not fav.is_file():
        return Response(status_code=204)
    return FileResponse(str(fav), headers={"Cache-Control": "public, max-age=86400"})

