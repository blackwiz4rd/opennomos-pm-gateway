#!/usr/bin/env bash
#
# Download the OpenAPI documents that PM providers publish for their own APIs.
#
# These are vendor-native contracts kept as reference material for adapter work.
# They are NOT the gateway's contract: spec/pm-api-openapi-v0.1.yaml remains the
# only source for generated server code. This script only downloads; run
# openapi-generator-cli yourself against a downloaded file if you want models.
#
set -euo pipefail

DEFAULT_OUT_DIR="${TMPDIR:-/tmp}/pm-gateway-openapi-specs"
OUT_DIR="$DEFAULT_OUT_DIR"
ONLY_PROVIDER=""
LIST_ONLY=0
CURL_TIMEOUT="${PM_GATEWAY_SPEC_CURL_TIMEOUT_SECONDS:-120}"

# provider|url|filename|note
#
# "url" is empty for providers whose spec exists but has no stable public URL,
# and for providers that publish no spec at all; those entries are printed as
# notes and skipped. Keep this catalog in sync with the "OpenAPI spec" column in
# the provider support table in README.md.
SPEC_CATALOG=(
    "jira|https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json|jira-openapi.json|"
    "confluence|https://developer.atlassian.com/cloud/confluence/swagger.v3.json|confluence-openapi.json|"
    "trello|https://developer.atlassian.com/cloud/trello/swagger.v3.json|trello-openapi.json|"
    "plane|https://developers.plane.so/api-reference/introduction|plane-openapi.json|self-hosted only: GET /api/schema/?format=openapi-json on your Plane instance"
    "planner|https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml|msgraph-v1.0-openapi.yaml|Microsoft Graph v1.0; Planner is a subset of this document"
    "asana|https://raw.githubusercontent.com/Asana/openapi/master/defs/asana_oas.yaml|asana-openapi.yaml|"
    "clickup|https://developer.clickup.com/openapi/clickup-api-v2-reference.json|clickup-api-v2-openapi.json|"
    "notion|https://raw.githubusercontent.com/makenotion/notion-mcp-server/main/scripts/notion-openapi.json|notion-openapi.json|unofficial: published from Notion's own MCP server repository"
    "basecamp|https://raw.githubusercontent.com/basecamp/basecamp-sdk/main/openapi.json|basecamp-openapi.json|"
    "github|https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json|github-api.github.com-openapi.json|"
    "smartsheet||smartsheet-openapi.yaml|spec exists but no stable public URL (Redocly-hosted, not directly fetchable)"
    "wrike||wrike-openapi.yaml|spec published July 2025 but no stable public URL (JS-rendered docs)"
    "gitlab||gitlab-openapi.yaml|partial spec exists in gitlab-org/gitlab but no stable public URL"
    "azure-devops||azure-devops-openapi.json|MicrosoftDocs/vsts-rest-api-specs is not anonymously reachable"
    "monday||monday-openapi.json|GraphQL only; no OpenAPI document published"
    "linear||linear-openapi.json|GraphQL only; no OpenAPI document published"
    "airtable||airtable-openapi.json|no official spec; only third-party generators"
    "zoho-projects||zoho-projects-openapi.json|Zoho publishes OAS for CRM/Desk/WorkDrive, not Projects"
    "microsoft-project||microsoft-project-openapi.json|no generic Project API; a product/API family must be selected first"
)

usage() {
    cat <<EOF
Usage: $0 [--out-dir DIR] [--provider NAME] [--list]

Download provider-published OpenAPI documents to one directory for reference.
Generated code for this package still comes only from
spec/pm-api-openapi-v0.1.yaml.

Options:
  --out-dir DIR      Destination directory (default: ${DEFAULT_OUT_DIR})
  --provider NAME    Download only this provider (repeatable: --provider jira --provider asana)
  --list             Print the catalog and exit without downloading
  -h, --help         Show this help

Environment:
  PM_GATEWAY_SPEC_CURL_TIMEOUT_SECONDS   Per-request curl timeout (default: 120)

Exit codes: 0 success, 1 a requested download failed, 2 invalid configuration.
EOF
}

fail_usage() {
    printf '%s\n' "$1" >&2
    usage >&2
    exit 2
}

PROVIDERS=()
while (($#)); do
    case "$1" in
        --out-dir)
            [[ $# -ge 2 ]] || fail_usage "--out-dir requires a value"
            OUT_DIR="$2"
            shift 2
            ;;
        --provider)
            [[ $# -ge 2 ]] || fail_usage "--provider requires a value"
            PROVIDERS+=("$2")
            shift 2
            ;;
        --list)
            LIST_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail_usage "Unknown argument: $1"
            ;;
    esac
done

if ((${#PROVIDERS[@]})); then
    for requested in "${PROVIDERS[@]}"; do
        found=0
        for entry in "${SPEC_CATALOG[@]}"; do
            if [[ "${entry%%|*}" == "$requested" ]]; then
                found=1
                break
            fi
        done
        if ((!found)); then
            printf 'Unknown provider: %s\n' "$requested" >&2
            printf 'Known providers: ' >&2
            for entry in "${SPEC_CATALOG[@]}"; do
                printf '%s ' "${entry%%|*}" >&2
            done
            printf '\n' >&2
            exit 2
        fi
    done
fi

provider_selected() {
    local candidate="$1"
    ((${#PROVIDERS[@]})) || return 0
    local requested
    for requested in "${PROVIDERS[@]}"; do
        [[ "$requested" == "$candidate" ]] && return 0
    done
    return 1
}

if ((LIST_ONLY)); then
    printf '%-18s %-10s %s\n' "PROVIDER" "STATUS" "SOURCE / NOTE"
    for entry in "${SPEC_CATALOG[@]}"; do
        IFS='|' read -r name url filename note <<<"$entry"
        if [[ -n "$url" ]]; then
            status="download"
            detail="$url"
        else
            status="skip"
            detail="$note"
        fi
        printf '%-18s %-10s %s\n' "$name" "$status" "$detail"
    done
    exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
    printf 'curl is required but was not found on PATH.\n' >&2
    exit 2
fi

mkdir -p -- "$OUT_DIR"
OUT_DIR="$(cd -- "$OUT_DIR" && pwd)"

printf 'Downloading provider OpenAPI specs into %s\n\n' "$OUT_DIR"

failures=0
skipped=0
downloaded=0

for entry in "${SPEC_CATALOG[@]}"; do
    IFS='|' read -r name url filename note <<<"$entry"
    provider_selected "$name" || continue

    if [[ -z "$url" ]]; then
        printf '  SKIP  %-16s %s\n' "$name" "$note"
        skipped=$((skipped + 1))
        continue
    fi

    target="${OUT_DIR}/${filename}"
    temp_file="$(mktemp "${OUT_DIR}/.${name}.XXXXXX")"
    if ! curl -fsSL --max-time "$CURL_TIMEOUT" --retry 2 --retry-delay 1 \
        -o "$temp_file" "$url"; then
        printf '  FAIL  %-16s %s\n' "$name" "$url"
        rm -f -- "$temp_file"
        failures=$((failures + 1))
        continue
    fi

    # A provider can answer 200 with an HTML error page. Require a document
    # marker so a redirect to a docs portal is not mistaken for a spec.
    if ! grep -Eq '"?openapi"?[": ]|^swagger:' "$temp_file" 2>/dev/null; then
        printf '  FAIL  %-16s response is not an OpenAPI document (see %s)\n' "$name" "$url"
        rm -f -- "$temp_file"
        failures=$((failures + 1))
        continue
    fi

    mv -f -- "$temp_file" "$target"
    size="$(wc -c <"$target" | tr -d '[:space:]')"
    printf '  OK    %-16s %s (%s bytes)\n' "$name" "$(basename -- "$target")" "$size"
    downloaded=$((downloaded + 1))
done

printf '\n%d downloaded, %d skipped, %d failed\n' "$downloaded" "$skipped" "$failures"
printf 'Note: these are provider-native contracts. Generated gateway code still comes from spec/pm-api-openapi-v0.1.yaml.\n'

if ((failures > 0)); then
    exit 1
fi
