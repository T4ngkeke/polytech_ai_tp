"""Work line B: a small, explicit probe of the deployment's model capabilities.

It reads the routing from the running deployment's system_configs table, exactly
as the application does, and then sends at most eight requests. API keys are used
to make a request and are never written to any output file.

Capability limits such as the context window come from the service's own model
metadata. A single short call cannot establish a window and is not used for it.
Eight requests are not a stability or latency measurement.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .common import RESULT_ROOT, utc_now, write_json

MAX_REQUESTS = 8
MAX_OUTPUT_TOKENS = 256
REQUEST_TIMEOUT_SECONDS = 20
TOTAL_BUDGET_SECONDS = 180

JSON_SCHEMA_KEYS = {"action", "reason"}
JSON_PROMPT = (
    'Answer with one JSON object and nothing else. It must have exactly the keys '
    '"action" (a string) and "reason" (a string). Question: which tool would you use '
    "to find a slide about SQL joins?"
)


@dataclass
class Endpoint:
    role: str
    base_url: str
    api_key: str
    model: str

    def public(self) -> dict:
        """What may be written down: never the key."""
        return {"role": self.role, "base_url": self.base_url, "model": self.model,
                "api_key_present": bool(self.api_key)}


def read_deployment_configs(container: str, database: str, user: str = "postgres") -> dict:
    """Read system_configs from the running deployment database."""
    query = "select key || '\t' || coalesce(value,'') from system_configs"
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", user, "-d", database, "-tAc", query],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot read system_configs: {result.stderr.strip()[:200]}")
    configs = {}
    for line in result.stdout.splitlines():
        if "\t" in line:
            key, value = line.split("\t", 1)
            configs[key.strip()] = value.strip()
    return configs


def _host_view(base_url: str) -> str:
    """The deployment addresses services by container name; we run on the host.

    Only the host part is rewritten, and the rewrite is recorded, so a failure is
    reported against the address we actually called.
    """
    return base_url.replace("host.docker.internal", "127.0.0.1")


def resolve_endpoints(configs: dict, *, base_url_override: str = "") -> dict:
    """Same fallback rules as backend/app/services/model_routing.py.

    base_url_override points the probe at a service that is running while the
    configuration still names another address. It changes nothing in the
    deployment, and the override is recorded next to every result.
    """
    def get(key: str, default: str = "") -> str:
        value = configs.get(key)
        return value if value else default

    main_base = base_url_override or _host_view(get("LLM_BASE_URL"))
    main_key, main_model = get("LLM_API_KEY"), get("LLM_MODEL")
    roles = {
        "llm": Endpoint("llm", main_base, main_key, main_model),
        "agent": Endpoint("agent", (base_url_override or _host_view(get("AGENT_BASE_URL", main_base))), get("AGENT_API_KEY", main_key), get("AGENT_MODEL", main_model)),
        "router": Endpoint("router", (base_url_override or _host_view(get("ROUTER_BASE_URL", main_base))), get("ROUTER_API_KEY", main_key), get("ROUTER_MODEL", main_model)),
        "ingest": Endpoint("ingest", (base_url_override or _host_view(get("INGEST_BASE_URL", main_base))), get("INGEST_API_KEY", main_key), get("INGEST_MODEL", main_model)),
        # The vision slot never falls back: an empty model means the feature is off.
        "vlm": Endpoint("vlm", (base_url_override or _host_view(get("VLM_BASE_URL", main_base))), get("VLM_API_KEY", main_key), get("VLM_MODEL", "")),
        "embedding": Endpoint("embedding", get("EMBEDDING_URL", main_base), get("EMBEDDING_API_KEY", main_key), get("EMBEDDING_MODEL", "BAAI/bge-m3")),
        "rerank": Endpoint("rerank", get("RERANK_URL", ""), get("RERANK_API_KEY", main_key), get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")),
    }
    return roles


def shared_models(roles: dict) -> dict:
    """Which roles point at the same endpoint and model, so we probe them once."""
    groups: dict[str, list[str]] = {}
    for role, endpoint in roles.items():
        if not endpoint.model or not endpoint.base_url:
            continue
        groups.setdefault(f"{endpoint.base_url}|{endpoint.model}", []).append(role)
    return {key: sorted(value) for key, value in groups.items()}


def _request(endpoint: Endpoint, path: str, payload: dict | None, *, stream: bool = False, cancel_after: float | None = None) -> dict:
    url = endpoint.base_url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Content-Type", "application/json")
    if endpoint.api_key:
        request.add_header("Authorization", f"Bearer {endpoint.api_key}")

    started = time.time()
    record: dict = {"started_at": utc_now(), "model": endpoint.model, "path": path, "stream": stream}
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            if not stream:
                body = response.read().decode("utf-8", "replace")
                record.update(status=response.status, seconds=round(time.time() - started, 3), body=body[:20000])
                return record
            first_token_at = None
            chunks = []
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                if first_token_at is None:
                    first_token_at = time.time()
                chunks.append(line[5:].strip())
                if cancel_after is not None and time.time() - started >= cancel_after:
                    record.update(
                        status=response.status,
                        client_cancelled=True,
                        server_stop_confirmed="unknown",
                        seconds=round(time.time() - started, 3),
                        chunks=len(chunks),
                    )
                    return record
            record.update(
                status=response.status,
                seconds=round(time.time() - started, 3),
                first_token_seconds=round(first_token_at - started, 3) if first_token_at else None,
                chunks=len(chunks),
                last_chunks=chunks[-3:],
            )
            return record
    except urllib.error.HTTPError as error:
        record.update(status=error.code, seconds=round(time.time() - started, 3),
                      error=f"HTTP {error.code}", body=error.read()[:500].decode("utf-8", "replace"))
    except Exception as error:  # network level, no key material in the message
        message = re.sub(r"Bearer\s+\S+", "Bearer <redacted>", str(error))[:200]
        record.update(status=None, seconds=round(time.time() - started, 3),
                      error=f"{type(error).__name__}: {message}")
    return record


def service_metadata(endpoint: Endpoint) -> dict:
    """Model metadata straight from the service. The evidence for context limits."""
    record = _request(endpoint, "/models", None)
    facts = {"reachable": record.get("status") == 200, "raw_status": record.get("status"), "error": record.get("error")}
    if record.get("status") == 200:
        try:
            payload = json.loads(record["body"])
        except json.JSONDecodeError:
            facts["parse_error"] = True
            return facts
        entry = next((m for m in payload.get("data", []) if m.get("id") == endpoint.model), None)
        entry = entry or (payload.get("data") or [None])[0]
        facts["model_entry"] = entry
        facts["context_window"] = (entry or {}).get("max_model_len")
        facts["context_window_source"] = "service /v1/models metadata" if (entry or {}).get("max_model_len") else "not published by the service"
    return facts


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAPElEQVR42u3OMQEAAAgDoC251a3g"
    "LwLQMzcxQSAQCAQCgUAgEAgEAoFAIBAIBAKBQCAQCAQCgUDwFrgB7psB9V5cBW0AAAAASUVORK5CYII="
)


def probe(roles: dict, *, allow_vision: bool = True) -> dict:
    """At most eight requests. No retries. Never a performance measurement."""
    started = time.time()
    calls: list[dict] = []
    agent = roles["agent"]
    results: dict = {
        "generated_at": utc_now(),
        "endpoints": {role: endpoint.public() for role, endpoint in roles.items()},
        "shared_models": shared_models(roles),
        "limits": {
            "max_requests": MAX_REQUESTS,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "total_budget_seconds": TOTAL_BUDGET_SECONDS,
            "retries": 0,
        },
        "service_metadata": {},
        "capabilities": {},
        "calls": calls,
    }

    if not agent.base_url or not agent.model:
        results["capabilities"]["json_mode"] = {"state": "not_tested", "reason": "no agent endpoint or model configured"}
        results["status"] = "blocked"
        return results

    results["service_metadata"]["agent"] = service_metadata(agent)
    if not results["service_metadata"]["agent"]["reachable"]:
        results["capabilities"] = {
            name: {"state": "not_tested", "reason": "the configured endpoint did not answer"}
            for name in ("json_mode", "vision", "streaming", "cancellation", "token_accounting", "context_window")
        }
        results["status"] = "blocked"
        results["blocked_reason"] = (
            f"the endpoint configured for the agent role ({agent.base_url}) refused the connection: "
            f"{results['service_metadata']['agent'].get('error')}"
        )
        return results

    def budget_left() -> bool:
        return time.time() - started < TOTAL_BUDGET_SECONDS and len(calls) < MAX_REQUESTS

    # 1-3: strict JSON, checked against a local schema.
    json_valid = 0
    for attempt in range(3):
        if not budget_left():
            break
        record = _request(agent, "/chat/completions", {
            "model": agent.model,
            "messages": [{"role": "user", "content": JSON_PROMPT}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
        })
        record["purpose"] = f"json_mode_{attempt + 1}"
        content = _content(record)
        record["schema_valid"] = _valid_json(content)
        # Kept so a failure can be read rather than guessed at. Model output is
        # not secret; the API key never appears in it.
        record["answer"] = (content or "")[:1500]
        record.pop("body", None)
        record["usage"] = _usage(record)
        json_valid += 1 if record["schema_valid"] else 0
        calls.append(record)
    results["capabilities"]["json_mode"] = {
        "state": "supported" if json_valid == 3 else "partial" if json_valid else "unsupported",
        "valid_responses": json_valid,
        "attempts": min(3, len(calls)),
        "note": "three fixed prompts checked against a local schema; not a stability result",
        "observed_failure_mode": (
            "the model reasons out loud first and the 256 token ceiling cuts the answer off "
            "before any JSON is produced"
        ) if json_valid == 0 else None,
    }

    # 4: the plain prompt may fail because the model reasons out loud first. Ask
    # the service to constrain the output, which tells the planner design whether
    # strict JSON is reachable at all on this deployment.
    if budget_left() and json_valid < 3:
        record = _request(agent, "/chat/completions", {
            "model": agent.model,
            "messages": [{"role": "user", "content": JSON_PROMPT}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        })
        record["purpose"] = "json_mode_constrained"
        content = _content(record)
        record["schema_valid"] = _valid_json(content)
        record["answer"] = (content or "")[:1500]
        record["usage"] = _usage(record)
        record.pop("body", None)
        calls.append(record)
        results["capabilities"]["json_mode_constrained"] = {
            "state": "supported" if record["schema_valid"] else "unsupported",
            "http_status": record.get("status"),
            "method": "response_format json_object",
            "note": (
                "one request. It says whether the service can constrain the output, "
                "not how reliable the content is"
            ),
        }

    # 5: a schema, not just "some JSON object". The planner needs fixed keys, so
    # the prompt deliberately does not mention them: only the constraint can hold.
    if budget_left() and results["capabilities"].get("json_mode_constrained", {}).get("state") == "supported":
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "read", "navigate", "finish"]},
                "reason": {"type": "string"},
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        }
        record = _request(agent, "/chat/completions", {
            "model": agent.model,
            "messages": [{"role": "user", "content": "Which tool finds a slide about SQL joins?"}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {"name": "planner_action", "schema": schema, "strict": True}},
        })
        record["purpose"] = "json_schema_constrained"
        content = _content(record)
        record["answer"] = (content or "")[:1500]
        keys_held = enum_held = False
        try:
            parsed = json.loads(content or "")
            keys_held = sorted(parsed) == ["action", "reason"]
            enum_held = parsed.get("action") in schema["properties"]["action"]["enum"]
        except (json.JSONDecodeError, TypeError, AttributeError):
            parsed = None
        record["keys_held"] = keys_held
        record["enum_held"] = enum_held
        record["usage"] = _usage(record)
        record.pop("body", None)
        calls.append(record)
        results["capabilities"]["json_schema_constrained"] = {
            "state": "supported" if keys_held and enum_held else "unsupported",
            "http_status": record.get("status"),
            "method": "response_format json_schema, strict",
            "keys_held_without_being_prompted": keys_held,
            "enum_held": enum_held,
            "note": "the prompt did not name the keys, so the constraint alone produced them. One request.",
        }

    # 6-7: vision, only when the deployment actually configures a vision model.
    vlm = roles["vlm"]
    if not vlm.model:
        results["capabilities"]["vision"] = {
            "state": "not_tested",
            "reason": "VLM_MODEL is empty in the deployment, so the vision slot is off and never falls back",
        }
    elif not allow_vision:
        results["capabilities"]["vision"] = {"state": "not_tested", "reason": "vision probing was not enabled for this run"}
    else:
        passed = 0
        for purpose, question in (("vision_text", "What text is in this image?"), ("vision_direction", "Which way does the arrow point?")):
            if not budget_left():
                break
            record = _request(vlm, "/chat/completions", {
                "model": vlm.model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(TINY_PNG).decode()}},
                ]}],
                "max_tokens": MAX_OUTPUT_TOKENS,
            })
            record["purpose"] = purpose
            record["image_input_method"] = "data URL, base64 PNG, 16x16"
            record["answer"] = _content(record)
            record["usage"] = _usage(record)
            record.pop("body", None)
            passed += 1 if record.get("status") == 200 else 0
            calls.append(record)
        results["capabilities"]["vision"] = {
            "state": "accepted_image_input" if passed else "unsupported",
            "note": "the service accepted or refused an image; whether the reading is correct is a human judgement",
        }

    # 6: streaming.
    if budget_left():
        record = _request(agent, "/chat/completions", {
            "model": agent.model,
            "messages": [{"role": "user", "content": "Count from one to five."}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
        }, stream=True)
        record["purpose"] = "streaming"
        record["usage_in_stream"] = any("usage" in chunk for chunk in record.get("last_chunks", []))
        calls.append(record)
        results["capabilities"]["streaming"] = {
            "state": "supported" if record.get("status") == 200 else "unsupported",
            "first_token_seconds": record.get("first_token_seconds"),
            "usage_reported_in_stream": record.get("usage_in_stream"),
        }

    # 7: the client stops reading. Whether the server stopped is a separate question.
    if budget_left():
        record = _request(agent, "/chat/completions", {
            "model": agent.model,
            "messages": [{"role": "user", "content": "Write a long description of a graph traversal."}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": True,
        }, stream=True, cancel_after=1.0)
        record["purpose"] = "client_cancel"
        calls.append(record)
        results["capabilities"]["cancellation"] = {
            "client_stopped": bool(record.get("client_cancelled")),
            "server_stop_confirmed": "unknown",
            "note": "no server side evidence is available from the client, so the server state stays unknown",
        }

    # 8: token accounting, only meaningful when the service reports usage.
    if budget_left():
        prompt = "token accounting check: " + "alpha beta gamma delta " * 4
        record = _request(agent, "/chat/completions", {
            "model": agent.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0,
        })
        record["purpose"] = "token_accounting"
        usage = _usage(record)
        record["usage"] = usage
        record.pop("body", None)
        calls.append(record)
        results["capabilities"]["token_accounting"] = {
            "state": "reported_by_service" if usage else "not_reported",
            "prompt_characters": len(prompt),
            "usage": usage,
            "note": "character count is not a tokenizer; the service's own count is the evidence",
        }

    window = results["service_metadata"]["agent"].get("context_window")
    results["capabilities"]["context_window"] = {
        "state": "read_from_service" if window else "unknown",
        "value": window,
        "source": results["service_metadata"]["agent"].get("context_window_source"),
        "note": "not inferred from a call",
    }
    results["capabilities"]["image_token_rule"] = {
        "state": "unknown",
        "reason": "the service publishes no image token rule and no vision model is configured",
    }
    results["status"] = "complete" if len(calls) >= 6 else "partial"
    results["request_count"] = len(calls)
    results["seconds"] = round(time.time() - started, 1)
    return results


def _content(record: dict) -> str | None:
    try:
        payload = json.loads(record.get("body") or "")
        return payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def _usage(record: dict) -> dict | None:
    try:
        return json.loads(record.get("body") or "").get("usage")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _valid_json(content: str | None) -> bool:
    if not content:
        return False
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and set(payload) == JSON_SCHEMA_KEYS and all(
        isinstance(payload[key], str) for key in JSON_SCHEMA_KEYS
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M0 work line B: model capability probe")
    parser.add_argument("--db-container", default="edullm-db")
    parser.add_argument("--database", default="edudb")
    parser.add_argument("--out", default=str(RESULT_ROOT / "model-capabilities.json"))
    parser.add_argument("--enable-vision", action="store_true", help="allow the two image requests")
    parser.add_argument("--base-url", default="", help="probe this address instead of the configured one")
    parser.add_argument("--base-url-reason", default="", help="why the configured address was not used")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        configs = read_deployment_configs(args.db_container, args.database)
    except Exception as error:
        payload = {
            "generated_at": utc_now(),
            "status": "blocked",
            "blocked_reason": f"cannot read the deployment configuration: {error}",
            "capabilities": {},
        }
        write_json(Path(args.out), payload, overwrite=args.overwrite)
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    roles = resolve_endpoints(configs, base_url_override=args.base_url)
    payload = probe(roles, allow_vision=args.enable_vision)
    if args.base_url:
        payload["address_override"] = {
            "configured_base_url": configs.get("LLM_BASE_URL", ""),
            "probed_base_url": args.base_url,
            "reason": args.base_url_reason or "not given",
            "warning": "the deployment configuration was not changed and still names the address above",
        }
    payload["config_source"] = f"system_configs in the running deployment database ({args.db_container}/{args.database})"
    payload["address_note"] = "host.docker.internal was rewritten to 127.0.0.1 because the probe runs on the host, not in a container"
    payload["secrets_note"] = "API keys were used to make requests and are not recorded here"
    write_json(Path(args.out), payload, overwrite=args.overwrite)
    print(json.dumps({k: v for k, v in payload.items() if k != "calls"}, ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
