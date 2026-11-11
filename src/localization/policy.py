"""Small request/reply helper shared by independent Forge capabilities."""

import json
from collections import OrderedDict

from forge_msgs import PolicyCommand, PolicyCommandStatus


class PolicyEndpoint:
    def __init__(self, node, policy_id):
        self.node, self.id, self.cache = node, policy_id, OrderedDict()

    def receive(self, event):
        command = PolicyCommand.from_arrow(event["value"])
        if command.policy_id != self.id:
            return None
        if not command.request_id:
            self.emit(command, "rejected", "request_id required", remember=False)
            return None
        fingerprint = (command.command, json.dumps(command.inputs(), sort_keys=True))
        if command.request_id in self.cache:
            previous, status = self.cache[command.request_id]
            if previous != fingerprint:
                self.emit(
                    command, "rejected", "request_id reused with different content", remember=False
                )
            else:
                self.node.send_output("status", status.to_arrow())
            return None
        return command

    def emit(self, command, status, message="", outputs=None, *, remember=True):
        value = PolicyCommandStatus.from_outputs(
            policy_id=self.id,
            command=command.command,
            request_id=command.request_id,
            status=status,
            message=message,
            outputs=outputs or {},
        )
        self.node.send_output("status", value.to_arrow())
        if remember and command.request_id:
            self.cache[command.request_id] = (
                (command.command, json.dumps(command.inputs(), sort_keys=True)),
                value,
            )
            while len(self.cache) > 128:
                self.cache.popitem(last=False)

    def runtime(self, status="running", message="", **outputs):
        self.node.send_output(
            "status",
            PolicyCommandStatus.from_outputs(
                policy_id=self.id,
                command="runtime",
                request_id="",
                status=status,
                message=message,
                outputs=outputs,
            ).to_arrow(),
        )
