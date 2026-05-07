"""健康检查"""

from fastapi import APIRouter

from app.backend.memory.cognee_admin import get_cognee_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.2.0", "cognee": get_cognee_status()}
