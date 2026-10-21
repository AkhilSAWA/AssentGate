"""Deterministic, non-overridable policy rules.

Policy output either blocks a call outright or sets a confirmation floor:
the call cannot be auto-allowed until the listed fields are approved.
Neither the classifier nor an approval can lift a block.

Confirmation floors are opt-in deployment choices. They are off by default
because judging whether an external recipient or a public share was
authorized is the classifier's job; a default floor would force a prompt the
model can never remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assentgate.schemas import AuthorizationRequest, ToolSpec

# Argument names that carry email addresses in AgentDojo's workspace tools.
RECIPIENT_FIELDS = ("recipients", "cc", "bcc", "email", "participants")


@dataclass
class PolicyConfig:
    blocked_tools: set[str] = field(default_factory=set)
    # Domains that may never receive data.
    blocked_domains: set[str] = field(default_factory=set)
    # Opt-in floor: side-effecting calls to recipients outside internal_domains
    # always need confirmation.
    confirm_external_recipients: bool = False
    internal_domains: set[str] = field(default_factory=set)
    # Opt-in floor: making content public always needs confirmation.
    confirm_public_visibility: bool = False


@dataclass
class PolicyVerdict:
    blocked_fields: list[str] = field(default_factory=list)
    confirm_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    blocked: bool = False


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


class HardPolicy:
    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()

    def evaluate(self, request: AuthorizationRequest, spec: ToolSpec | None) -> PolicyVerdict:
        verdict = PolicyVerdict()
        call = request.tool_call

        if spec is None:
            verdict.blocked = True
            verdict.reasons.append(f"Unknown tool '{call.tool}'.")
            return verdict

        if call.tool in self.config.blocked_tools:
            verdict.blocked = True
            verdict.reasons.append(f"Tool '{call.tool}' is blocked by policy.")
            return verdict

        missing = [a for a in spec.required_args if a not in call.arguments]
        if missing:
            verdict.blocked = True
            verdict.blocked_fields = sorted(missing)
            verdict.reasons.append(f"Missing required arguments: {', '.join(missing)}.")
            return verdict

        declared = set(spec.required_args) | set(spec.optional_args)
        undeclared = sorted(a for a in call.arguments if a not in declared)
        if undeclared:
            verdict.blocked = True
            verdict.blocked_fields = undeclared
            verdict.reasons.append(f"Undeclared arguments: {', '.join(undeclared)}.")
            return verdict

        for name in RECIPIENT_FIELDS:
            for address in _as_list(call.arguments.get(name)):
                domain = _domain(address)
                if domain in self.config.blocked_domains:
                    verdict.blocked = True
                    verdict.blocked_fields.append(name)
                    verdict.reasons.append(f"Recipient '{address}' is in a blocked domain.")
                elif (
                    self.config.confirm_external_recipients
                    and spec.side_effecting
                    and domain not in self.config.internal_domains
                ):
                    verdict.confirm_fields.append(name)
                    verdict.reasons.append(f"Recipient '{address}' is external.")

        if (
            self.config.confirm_public_visibility
            and str(call.arguments.get("visibility", "")).lower() == "public"
        ):
            verdict.confirm_fields.append("visibility")
            verdict.reasons.append("Making content public requires confirmation.")

        verdict.blocked_fields = sorted(set(verdict.blocked_fields))
        verdict.confirm_fields = sorted(set(verdict.confirm_fields))
        return verdict
