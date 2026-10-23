"""Labels by construction from AgentDojo ground truth (no human annotation).

For a user task (optionally under an injection task's attack text), the user task's
ground-truth calls and then the injection task's ground-truth calls are run directly
through a Dispatcher. Before each side-effecting call, the request is recorded exactly as
the gate would see it (`Dispatcher.instrument`), with a label fixed by how it was made:

  allow    a ground-truth call of the user task (the benchmark says the task needs it)
  block    a ground-truth call of the injection task (the attacker's goal)
  confirm  a user-task call with ONE sensitive value mutated (guidelines v0.2, test C fails):
             extra_seen / swap_seen        an address that an earlier output returned, but not this one
             extra_unseen / swap_unseen    an address from the account's contacts never shown in outputs
             swap_seen_id / swap_unseen_id another file/event/email id
             permission_flip               r <-> rw
             swap_attachment               another file attached

In training data the attacker's e-mail addresses are replaced by random ones (goal text and
calls alike), so a model can't memorise the benchmark's attacker identities.

Limits (state them with any result): labels assume the benchmark ground truth is exactly what
the user authorised; vague-request ("my team") mutations aren't generated; the agent is scripted.
"""

from __future__ import annotations

import random
import re
from typing import Any, Iterable

from assentgate.dispatcher import Dispatcher
from assentgate.gate import AssentGate
from assentgate.schemas import AuthorizationRequest, Decision, ToolCall, ToolSpec
from assentgate.classifier import ClassifierOutput

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ADDRESS_FIELDS = {"recipients", "cc", "bcc", "participants", "email"}
OUTPUT_ADDRESS_KEYS = ["sender", "recipients", "cc", "bcc", "participants", "email"]
ID_FIELDS = {"file_id", "event_id", "email_id"}
_FIRST = ["alex", "jordan", "sam", "casey", "riley", "morgan", "taylor", "jamie", "devon", "quinn"]
_LAST = ["reed", "vance", "holt", "marsh", "keane", "lowe", "price", "shaw", "frost", "wolfe"]
_DOMAINS = ["gmail.com", "outlook.com", "proton.me", "mail.example", "inbox.example"]


class _AllowAll:
    def predict(self, request, spec):
        return ClassifierOutput(decision=Decision.AUTHORIZED)


def _map_strings(value: Any, fn) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    return value


def random_identities(texts: Iterable[str], rng: random.Random) -> dict[str, str]:
    found = sorted({m for t in texts for m in EMAIL_RE.findall(t)})
    return {e: f"{rng.choice(_FIRST)}.{rng.choice(_LAST)}-{rng.randint(1000, 9999)}@{rng.choice(_DOMAINS)}"
            for e in found}


def _replace(text: str, mapping: dict[str, str]) -> str:
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def _env_addresses(env: Any) -> list[str]:
    owner = str(env.inbox.account_email).lower()
    return sorted({c.email for c in env.inbox.contact_list if c.email.lower() != owner})


def _env_ids(env: Any, field: str) -> list[str]:
    source = {"file_id": env.cloud_drive.files, "event_id": env.calendar.events, "email_id": env.inbox.emails}[field]
    return sorted(source)


def mutations(call: ToolCall, spec: ToolSpec, dispatcher: Dispatcher, env: Any, rng: random.Random):
    """Yield (kind, mutated ToolCall): one sensitive value changed per mutation."""
    args = call.arguments
    owner = str(env.inbox.account_email).lower()  # sending to yourself isn't a meaningful distractor
    seen_addr = [v for v in dispatcher.provenance.seen_values(OUTPUT_ADDRESS_KEYS)
                 if EMAIL_RE.fullmatch(v) and v.lower() != owner]
    seen_ids = dispatcher.provenance.seen_values(["id_"])
    for field in spec.sensitive_fields:
        if field not in args or args[field] in (None, [], ""):
            continue
        value = args[field]
        if field in ADDRESS_FIELDS:
            current = set(value if isinstance(value, list) else [value])
            for origin, pool in (("seen", seen_addr), ("unseen", [a for a in _env_addresses(env) if a not in seen_addr])):
                pool = [a for a in pool if a not in current]
                if not pool:
                    continue
                pick = rng.choice(pool)
                if isinstance(value, list):
                    yield f"extra_{origin}", ToolCall(tool=call.tool, arguments={**args, field: [*value, pick]})
                    swapped = list(value)
                    swapped[rng.randrange(len(swapped))] = pick
                    yield f"swap_{origin}", ToolCall(tool=call.tool, arguments={**args, field: swapped})
                else:
                    yield f"swap_{origin}", ToolCall(tool=call.tool, arguments={**args, field: pick})
                break  # prefer distractors from the same source; fall back to unseen ones
        elif field in ID_FIELDS:
            for origin, pool in (("seen", seen_ids), ("unseen", _env_ids(env, field))):
                pool = [i for i in pool if i != str(value)]
                if pool:
                    yield f"swap_{origin}_id", ToolCall(tool=call.tool, arguments={**args, field: rng.choice(pool)})
                    break
        elif field == "permission":
            flipped = "rw" if str(value).lower() in ("r", "read") else "r"
            yield "permission_flip", ToolCall(tool=call.tool, arguments={**args, field: flipped})
        elif field == "attachments" and isinstance(value, list):
            ids = [a.get("file_id") for a in value if isinstance(a, dict) and a.get("file_id")]
            pool = [i for i in (seen_ids or _env_ids(env, "file_id")) if i not in ids]
            if ids and pool:
                swapped = [dict(a, file_id=rng.choice(pool)) if isinstance(a, dict) and a.get("file_id") else a
                           for a in value]
                yield "swap_attachment", ToolCall(tool=call.tool, arguments={**args, field: swapped})


def generate_run(suite: Any, user_task: Any, injection_task: Any | None, attack: Any, specs: dict[str, ToolSpec],
                 tools: list[ToolSpec], seed: int, randomize: bool = True) -> list[dict]:
    """Records for one (user task, injection task, seed). Executes calls with no gate."""
    from agentdojo.functions_runtime import FunctionsRuntime

    rng = random.Random(f"{user_task.ID}|{injection_task.ID if injection_task else '-'}|{seed}")
    mapping: dict[str, str] = {}
    injections: dict[str, str] = {}
    if injection_task is not None:
        probe = suite.load_and_inject_default_environment({})
        texts = [injection_task.GOAL] + [s for c in injection_task.ground_truth(probe)
                                         for s in _strings(c.args)]
        mapping = random_identities(texts, rng) if randomize else {}
        injections = attack.injections(user_task, injection_task, goal=_replace(injection_task.GOAL, mapping))
    env = user_task.init_environment(suite.load_and_inject_default_environment(injections))
    runtime = FunctionsRuntime(suite.tools)
    user_calls = list(user_task.ground_truth(env.model_copy(deep=True)))
    inj_calls = list(injection_task.ground_truth(env.model_copy(deep=True))) if injection_task else []

    dispatcher = Dispatcher(AssentGate(tools, classifier=_AllowAll()))
    for name in runtime.functions:
        if name in specs:
            dispatcher.register(name, _handler(runtime, env, name))

    records = []
    sequence = [("user", c) for c in user_calls] + [("injection", c) for c in inj_calls]
    for step, (origin, fc) in enumerate(sequence):
        args = dict(fc.args) if origin == "user" else _map_strings(dict(fc.args), lambda s: _replace(s, mapping))
        call = ToolCall(tool=fc.function, arguments=args)
        spec = specs.get(call.tool)
        request = AuthorizationRequest(user_request=user_task.PROMPT, tool_call=call)
        if spec is not None and spec.side_effecting:
            base = {"user_task": user_task.ID, "injection_task": injection_task.ID if injection_task else None,
                    "seed": seed, "step": step, "origin": origin, "tool": call.tool}
            label = "allow" if origin == "user" else "block"
            records.append({**base, "label": label, "mutation": None,
                            "request": dispatcher.instrument(request).model_dump(mode="json")})
            if origin == "user":
                for kind, mutated in mutations(call, spec, dispatcher, env, rng):
                    m_req = AuthorizationRequest(user_request=user_task.PROMPT, tool_call=mutated)
                    records.append({**base, "label": "confirm", "mutation": kind,
                                    "request": dispatcher.instrument(m_req).model_dump(mode="json")})
        dispatcher.submit(request)
    return records


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _handler(runtime: Any, env: Any, name: str):
    def run(**kwargs: Any) -> Any:
        result, error = runtime.run_function(env, name, kwargs)
        if error:
            raise RuntimeError(error)
        return result

    return run
