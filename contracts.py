"""Single source of truth for runtime and artifact contracts.

Every server module and Kaggle contract probe imports version values from this
module. Do not duplicate literal contract strings in router/providers/agent.
"""

APP_BUILD_VERSION = "multi-provider-asr-v7.0"
ROUTER_BUILD_VERSION = "router-latency-v3.1"
ROUTER_CONTRACT_VERSION = "routing-score-v2"
PROVIDER_REGISTRY_CONTRACT = "provider-registry-v2"
AGENT_CONTRACT_VERSION = PROVIDER_REGISTRY_CONTRACT
ASR_INDEX_CONTRACT_VERSION = "frame-docs-fts-v1"
ASR_MANIFEST_SCHEMA_VERSION = 2
ASR_SQLITE_USER_VERSION = 1


def public_contracts() -> dict[str, object]:
    return {
        "app_build_version": APP_BUILD_VERSION,
        "router_build_version": ROUTER_BUILD_VERSION,
        "router_contract_version": ROUTER_CONTRACT_VERSION,
        "provider_registry_contract": PROVIDER_REGISTRY_CONTRACT,
        "agent_contract_version": AGENT_CONTRACT_VERSION,
        "asr_index_contract_version": ASR_INDEX_CONTRACT_VERSION,
        "asr_manifest_schema_version": ASR_MANIFEST_SCHEMA_VERSION,
        "asr_sqlite_user_version": ASR_SQLITE_USER_VERSION,
    }
