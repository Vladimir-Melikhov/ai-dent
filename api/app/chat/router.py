import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..db.models import Conversation
from .protocol import error
from .service import CLOSE_BAD_TICKET, CLOSE_CONVERSATION_CLOSED, CLOSE_FORBIDDEN, ChatService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket, ticket: str = ""):
    service: ChatService = websocket.app.state.chat
    settings = get_settings()

    # Чужой сайт не может открыть соединение от имени клиента
    if websocket.headers.get("origin", "").rstrip("/") != settings.site_origin.rstrip("/"):
        await websocket.close(code=CLOSE_FORBIDDEN)
        return

    # Сначала accept, потом close с кодом: так браузер получает причину и знает, брать ли новый тикет
    await websocket.accept()
    owner = await service.redeem_ticket(ticket)
    if owner is None:
        await websocket.close(code=CLOSE_BAD_TICKET)
        return
    conversation = await service.conversation_for(owner)
    if conversation is None:
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    if conversation.status != Conversation.ACTIVE:
        await websocket.close(code=CLOSE_CONVERSATION_CLOSED)
        return

    cid = owner.conversation_id
    service.hub.add(cid, websocket)
    try:
        await websocket.send_json({
            "type": "history",
            "conversation_id": cid,
            "messages": await service.history(cid),
        })
        while True:
            try:
                data = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                await websocket.send_json(error("bad_request"))
                continue
            kind = data.get("type") if isinstance(data, dict) else None
            if kind == "user_message":
                await service.on_user_message(websocket, owner, data)
            elif kind == "stop":
                service.on_stop(owner)
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json(error("bad_request"))
    except WebSocketDisconnect:
        pass
    finally:
        service.hub.remove(cid, websocket)
