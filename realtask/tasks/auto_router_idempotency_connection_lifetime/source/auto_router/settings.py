from functools import lru_cache
import os
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


def validate_database_placement(
    configured_url: str,
    *,
    expected_root: str,
    required: bool,
    test_context: bool = False,
) -> dict[str, Any]:
    """Resolve and validate the SQLite path used by the router.

    The deployment contract treats ``sqlite:///data/...`` as the absolute
    ``/data/...`` persistent mount. Dot-relative paths are resolved from the
    process workdir so the historical ``/app/data`` mistake is visible.
    """
    root = os.path.abspath(expected_root)
    prefix = "sqlite:///"
    if not configured_url.startswith(prefix):
        return {
            "configured_url": configured_url,
            "resolved_path": None,
            "expected_persistent_root": root,
            "persistent": None,
            "supported": False,
        }

    raw_path = configured_url[len(prefix):]
    if raw_path == ":memory:":
        resolved_path = ":memory:"
    elif raw_path.startswith("/"):
        resolved_path = os.path.abspath(raw_path)
    elif raw_path.startswith(("./", "../")):
        resolved_path = os.path.abspath(raw_path)
    else:
        # This is the deployment contract used by Compose: sqlite:///data/x
        # means /data/x, while sqlite:///./data/x remains workdir-relative.
        resolved_path = os.path.abspath(os.path.join(os.sep, raw_path))

    persistent = resolved_path != ":memory:" and (
        resolved_path == root or resolved_path.startswith(root + os.sep)
    )
    result = {
        "configured_url": configured_url,
        "resolved_path": resolved_path,
        "expected_persistent_root": root,
        "persistent": persistent,
        "supported": True,
    }
    if required and not persistent and not test_context:
        raise ValueError(
            "SQLite database placement is not persistent: "
            f"configured URL={configured_url!r}, resolved path={resolved_path!r}, "
            f"expected persistent root={root!r}; refusing container writable layer"
        )
    return result


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="AUTO_ROUTER_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8088
    provider_config: str = "config/providers.yaml"
    policy_config: str = "config/policies.yaml"
    agent_config: str = "config/agent_workers.yaml"
    context_config: str = "config/context.yaml"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///./data/router.sqlite3"
    database_persistent_root: str = "/data"
    database_placement_required: bool = False
    database_placement_test_context: bool = False
    log_prompts: bool = False
    default_profile: str = "interactive_balanced"
    request_timeout_seconds: float = 240.0
    # Fail-fast routing: a single hung/dead node must not stall a request for the
    # full request_timeout. We connect quickly (so unreachable nodes fail fast) and
    # cap each individual candidate attempt at attempt_timeout_seconds, letting the
    # router fail over to the next candidate instead of waiting on a zombie.
    attempt_timeout_seconds: float = 240.0
    connect_timeout_seconds: float = 5.0
    # Hard bounds on a single request so a fleet full of dead nodes can never make
    # a request hang for (attempt_timeout * candidate_count). Once the deadline is
    # hit or we've tried enough candidates, we stop and return 503 fast.
    request_deadline_seconds: float = 300.0
    max_candidate_attempts: int = 4
    # Persisted per-node latency EMA so the router remembers which nodes are snappy
    # across restarts (instead of re-learning from a cold start every boot). The
    # path is derived from the database location so it lands in the same (mounted,
    # durable) directory as router.sqlite3 in both docker and local runs.
    latency_persist_interval_seconds: int = 30
    # Weight applied to the latency term in fleet load-balancing. 1.0 = default
    # (latency-aware: slow nodes are deprioritized). 0.0 = ignore latency entirely
    # and spread `auto` traffic evenly across all healthy nodes by in-flight load.
    # Set ROUTER_LATENCY_TERM_WEIGHT=0 to force full fleet utilization.
    latency_term_weight: float = 0.0

    @property
    def latency_cache_path(self) -> str:
        db = self.database_url
        if db.startswith("sqlite:///"):
            db_path = db[len("sqlite:///"):]
            if not os.path.isabs(db_path):
                db_path = os.path.join(os.getcwd(), db_path)
            return os.path.join(os.path.dirname(os.path.abspath(db_path)), "latency_ema.json")
        return os.path.join(os.getcwd(), "data", "latency_ema.json")
    live_model_cache_ttl_seconds: int = 3600
    live_model_poll_interval_seconds: float = 5.0
    assistx_event_sink_url: str | None = None
    assistx_event_dispatch_timeout_seconds: float = 10.0
    assistx_event_dispatch_max_attempts: int = 5
    assistx_event_dispatch_interval_seconds: float = 300.0
    assistx_event_dispatch_batch_max: int = 200
    assistx_outbox_warning_threshold: int = 25
    assistx_outbox_critical_threshold: int = 200
    assistx_tasks_url: str | None = None
    assistx_tasks_timeout_seconds: float = 10.0
    # Fleet experience memory. The remote endpoint is expected to be backed by
    # AssistX/Neo4j. Empty keeps the router in local degraded mode so memory
    # never becomes a hard dependency for routing.
    memory_enabled: bool = True
    memory_service_url: str | None = None
    memory_timeout_seconds: float = 5.0
    memory_context_budget_tokens: int = 3000
    memory_query_limit: int = 8
    backlog_burn_interval_seconds: int = 60
    assistx_basic_auth_user: str = ""
    assistx_basic_auth_pass: str = ""
    # In-process agent execution. This is TRANSITIONAL: the canonical execution
    # authority is (Paperclip via) auto-assist. When disabled (default), tool-capable
    # route decisions still produce a RouteDecision + job record metadata but the
    # router does NOT launch in-process workers; callers should dispatch to Paperclip.
    fleet_dispatcher_enabled: bool = False
    # OpenTelemetry tracing. Engaged only when true (LLD §3.5 W-60); requires the
    # optional opentelemetry-api SDK to be installed.
    otel_enabled: bool = False
    # Admin/ops endpoint protection (LLD §3.5 W-64). When set, the /admin/* and
    # /jobs/agent endpoints require this shared token via the X-Admin-Token header
    # or HTTP Basic auth (user "admin", this token as password). Empty = locked down
    # (requests rejected until a token is configured).
    admin_token: str = ""




@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    validate_database_placement(
        settings.database_url,
        expected_root=settings.database_persistent_root,
        required=settings.database_placement_required,
        test_context=settings.database_placement_test_context,
    )
    return settings
