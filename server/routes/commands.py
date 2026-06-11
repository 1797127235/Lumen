"""斜杠命令 API — TUI 可用命令列表和执行"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from lib.chat.persistence import ensure_conversation
from lib.skills.loader import get_skills_loader
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/commands", tags=["commands"])


class CommandItem(BaseModel):
    name: str
    description: str
    arg_required: bool = False


class CommandExecuteRequest(BaseModel):
    command: str
    arguments: str
    session_id: str | None = None


class CommandExecuteResponse(BaseModel):
    ok: bool
    action: str | None = None
    session_id: str | None = None
    title: str | None = None
    text: str | None = None
    response: str | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None


@router.get("/list", response_model=list[CommandItem])
async def list_commands():
    loader = get_skills_loader()
    skills = loader.list_skills(filter_unavailable=True)

    skill_commands = [
        CommandItem(
            name=s["name"],
            description=f"加载技能：{s['description']}",
            arg_required=False,
        )
        for s in skills
    ]

    session_commands = [
        CommandItem(name="new", description="创建新会话"),
        CommandItem(name="resume", description="恢复指定会话", arg_required=True),
        CommandItem(name="delete", description="删除会话", arg_required=True),
        CommandItem(name="rename", description="重命名会话", arg_required=True),
        CommandItem(name="model", description="切换模型，无参数时打开选择列表"),
        CommandItem(name="exit", description="退出 TUI"),
        CommandItem(name="quit", description="退出 TUI（exit 别名）"),
        CommandItem(name="help", description="显示帮助信息"),
    ]

    return session_commands + skill_commands


@router.post("/execute", response_model=CommandExecuteResponse)
async def execute_command(req: CommandExecuteRequest, db: AsyncSession = Depends(get_db)):
    cmd = req.command
    args = req.arguments or ""

    logger.info("执行命令", command=cmd, args=args)

    # ensure_conversation 真实签名：(db, user_id, conversation_id, user_input)
    if cmd == "new":
        result = await ensure_conversation(db, "demo_user", None, "")
        if isinstance(result, str):
            return CommandExecuteResponse(ok=False, error=result)
        await db.commit()
        return CommandExecuteResponse(ok=True, action="switch", session_id=result.conversation_id)

    if cmd == "model":
        if not args:
            # 无参数 → 告诉 TUI 打开选择对话框
            return CommandExecuteResponse(ok=True, action="model_list")
        # 有参数：provider/model 或直接 model
        if "/" in args:
            provider_arg, _, model_arg = args.partition("/")
        else:
            provider_arg, model_arg = None, args.strip()
        return CommandExecuteResponse(
            ok=True,
            action="model_set",
            provider=provider_arg.strip() if provider_arg else None,
            model=model_arg.strip(),
        )

    if cmd in ("exit", "quit"):
        return CommandExecuteResponse(ok=True, action="exit")

    if cmd == "help":
        commands = await list_commands()
        help_text = "## 可用命令\n\n" + "\n".join(
            f"**{c.name}** - {c.description}" + (" `<参数>`" if c.arg_required else "") for c in commands
        )
        return CommandExecuteResponse(ok=True, action="help", response=help_text)

    if cmd in ("resume", "delete", "rename"):
        if not args:
            return CommandExecuteResponse(ok=False, error=f"/{cmd} 需要参数")
        sep = args.find(" ")
        session_id_arg = args[:sep].strip() if sep != -1 else args.strip()
        remaining = args[sep + 1 :].strip() if sep != -1 else ""

        if cmd == "resume":
            return CommandExecuteResponse(ok=True, action="switch", session_id=session_id_arg)
        if cmd == "delete":
            return CommandExecuteResponse(ok=True, action="delete", session_id=session_id_arg)
        if cmd == "rename":
            if not remaining:
                return CommandExecuteResponse(ok=False, error="/rename 需要两个参数：<session_id> <title>")
            return CommandExecuteResponse(ok=True, action="rename", session_id=session_id_arg, title=remaining)

    # 技能命令
    loader = get_skills_loader()
    skills = {s["name"] for s in loader.list_skills(filter_unavailable=True)}
    if cmd in skills:
        skill_text = f"${cmd}" + (f" {args}" if args else "")
        return CommandExecuteResponse(ok=True, action="skill", text=skill_text)

    return CommandExecuteResponse(ok=False, error=f"未知命令：/{cmd}")
