from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, Discriminator


class CoreStartedEvent(BaseModel):
    type: Literal["core.started"] = "core.started"
    listen_addr: str
    version: str


Event = Annotated[CoreStartedEvent, Discriminator("type")]
