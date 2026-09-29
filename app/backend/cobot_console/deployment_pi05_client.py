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
        self.pause_source = "load"
        self.pause_caller = ""
        self.path = Path(os.environ["COBOT_PI05_GATE_STATE"])
        super().__init__(*args, **kwargs)
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 2, "paused": self.paused, "manual_pause": self.manual_pause,
                                         "hil_active": self.hil_active, "pause_source": self.pause_source,
                                         "pause_caller": self.pause_caller,
                                         "intervention_count": self.intervention_count, "updated_at": time.time()}))
        os.replace(temporary, self.path)

    def handle_set_paused(self, request):
        caller = str(getattr(request, "_connection_header", {}).get("callerid", ""))
        node = caller.rsplit("/", 1)[-1]
        operator = node == "cobot_deployment_command" or node.startswith("cobot_deployment_command_")
        coordinator = node in {"task2_teach_button_handover", "task2_teach_handover_node"}
        requested = bool(request.data)
        with self._lock:
            if operator:
                self.manual_pause = requested
                source = "operator"
            elif coordinator:
                if requested and not self.hil_active:
                    self.intervention_count += 1
                self.hil_active = requested
                source = "handover"
            else:
                # Recover / home / rosservice may pause for protection. They must
                # not become HIL, nor release an operator or protective pause.
                if requested:
                    self.manual_pause = True
                source = "protective"
            self.pause_source, self.pause_caller = source, caller
            request.data = self.manual_pause or self.hil_active
            result = super().handle_set_paused(request)
            if not requested and ((operator and self.hil_active) or (not operator and not coordinator)):
                result.success = False
                result.message = ("Teaching is still active; release the teach button before Continue"
                                  if operator else "Protective pause can only be released by explicit operator Continue")
            self.save()
            return result


client.Task2PauseGate = ConsolePauseGate
if __name__ == "__main__":
    raise SystemExit(client.main())
