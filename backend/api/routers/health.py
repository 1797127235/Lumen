"""健康检查"""

from fastapi import APIRouter

from backend.memory import get_memory

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.2.0", "cognee": get_memory().cognee_status()}
