from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, Discriminator

class PingCommand(BaseModel):
    type: Literal["core.ping"] = "core.ping"
    client: str
    
class PongResult(BaseModel):
    server_version : str
    uptime_ms : int
    received_at : str

Command = Annotated[PingCommand, Discriminator("type")]