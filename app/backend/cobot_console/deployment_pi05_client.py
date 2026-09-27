"""Legacy RTC deployment with an observable operator pause latch."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ["COBOT_PI05_CLIENT_ROOT"])
import inference_pi05_rtc_task2 as client

OriginalGate = client.Task2PauseGate


class ConsolePauseGate(OriginalGate):
    def __init__(self, *args, **kwargs):
        self.manual_pause = True
        self.intervention_count = 0
        self.hil_active = False
        self.path = Path(os.environ["COBOT_PI05_GATE_STATE"])
        super().__init__(*args, **kwargs)
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"paused": self.paused, "manual_pause": self.manual_pause,
                                         "intervention_count": self.intervention_count, "updated_at": time.time()}))
        os.replace(temporary, self.path)

    def handle_set_paused(self, request):
        caller = str(getattr(request, "_connection_header", {}).get("callerid", ""))
        with self._lock:
            if "cobot_deployment_command" in caller:
                self.manual_pause = bool(request.data)
            else:
                if request.data and not self.hil_active:
                    self.intervention_count += 1
                self.hil_active = bool(request.data)
            request.data = self.manual_pause or self.hil_active
            result = super().handle_set_paused(request)
            self.save()
            return result


client.Task2PauseGate = ConsolePauseGate
if __name__ == "__main__":
    raise SystemExit(client.main())
