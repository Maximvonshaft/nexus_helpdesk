import asyncio
import uuid
import logging

class LiteNotifier:
    def __init__(self):
        self.listeners = {}
        self._lock = asyncio.Lock()

    async def subscribe(self):
        queue = asyncio.Queue()
        listener_id = uuid.uuid4()
        async with self._lock:
            self.listeners[listener_id] = queue
        return listener_id, queue

    async def unsubscribe(self, listener_id):
        async with self._lock:
            if listener_id in self.listeners:
                del self.listeners[listener_id]

    def notify_all_sync(self, event_data: str = "update"):
        """Called from sync synchronous SQLAlchemy service layer."""
        # Try to get the running loop
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._notify_async, event_data)
        except RuntimeError:
            # If no running loop, we skip notifications
            pass

    def _notify_async(self, event_data: str):
        for q in self.listeners.values():
            try:
                q.put_nowait(event_data)
            except Exception as e:
                logging.error(f"Failed to push SSE event: {e}")

lite_notifier = LiteNotifier()
