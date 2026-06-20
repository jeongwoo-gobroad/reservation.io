from __future__ import annotations

import json
from typing import Protocol

from .config import Settings


class MessageBus(Protocol):
    def publish(self, event_type: str, payload: dict) -> None:
        ...


class NullMessageBus:
    def publish(self, event_type: str, payload: dict) -> None:
        return None


class PubSubMessageBus:
    def __init__(self, topic: str) -> None:
        from google.cloud import pubsub_v1

        self.topic = topic
        self.publisher = pubsub_v1.PublisherClient()

    def publish(self, event_type: str, payload: dict) -> None:
        data = json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False, default=str).encode("utf-8")
        self.publisher.publish(self.topic, data, event_type=event_type)


def get_message_bus(settings: Settings) -> MessageBus:
    backend = settings.message_bus_backend.strip().lower()
    if backend == "pubsub" and settings.visit_event_topic:
        return PubSubMessageBus(settings.visit_event_topic)
    return NullMessageBus()

