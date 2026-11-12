#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SPEC_DIR="${TMPDIR:-/tmp}/pm-gateway-openapi-specs"
OUTPUT_DIR="${PACKAGE_DIR}/src/pm_gateway/generated/providers"
GENERATOR_CLI="${OPENAPI_GENERATOR_CLI:-openapi-generator-cli}"
GENERATOR_VERSION="${OPENAPI_GENERATOR_VERSION:-7.26.0}"

while (($#)); do
    case "$1" in
        --spec-dir)
            SPEC_DIR="$2"
            shift 2
            ;;
        --out-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if ! command -v "$GENERATOR_CLI" >/dev/null 2>&1; then
    echo "missing generator" >&2
    exit 1
fi

TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pm-gateway-provider-clients.XXXXXX")"
trap 'rm -rf -- "$TEMP_ROOT"' EXIT

PROVIDERS=(
  "trello|trello-openapi.json|/members/{id}/boards=GET;/boards/{id}=GET;/boards/{id}/lists=GET;/boards/{id}/cards=GET;/cards/{id}=GET,PUT"
  "basecamp|basecamp-openapi.json|/{accountId}/projects.json=GET;/{accountId}/projects/{projectId}=GET;/{accountId}/todosets/{todosetId}=GET;/{accountId}/todosets/{todosetId}/todolists.json=GET,POST;/{accountId}/todolists/{todolistId}/todos.json=GET,POST;/{accountId}/todos/{todoId}=GET,PUT;/{accountId}/todos/{todoId}/completion.json=POST,DELETE"
  "github|github-api.github.com-openapi.json|/user/repos=GET;/users/{username}/repos=GET;/orgs/{org}/repos=GET;/repos/{owner}/{repo}=GET;/repos/{owner}/{repo}/issues=GET,POST;/repos/{owner}/{repo}/issues/{issue_number}=GET,PATCH;/repos/{owner}/{repo}/labels=GET;/repos/{owner}/{repo}/issues/{issue_number}/labels=GET,POST,PUT"
  "clickup|clickup-api-v2-openapi.json|/v2/team=GET;/v2/team/{team_id}/space=GET;/v2/space/{space_id}/folder=GET;/v2/space/{space_id}/list=GET;/v2/folder/{folder_id}/list=GET;/v2/list/{list_id}=GET;/v2/list/{list_id}/task=GET;/v2/task/{task_id}=GET,PUT|modelNameSuffix=Model"
  "asana|asana-openapi.yaml|/workspaces/{workspace_gid}/projects=GET;/projects/{project_gid}=GET;/projects/{project_gid}/sections=GET;/tasks=GET;/tasks/{task_gid}=GET,PUT;/sections/{section_gid}/addTask=POST"
  "jira|jira-openapi.json|/rest/api/3/project/search=GET;/rest/api/3/project/{projectIdOrKey}=GET;/rest/api/3/search/jql=GET;/rest/api/3/issue/{issueIdOrKey}=GET;/rest/api/3/issue/{issueIdOrKey}/transitions=GET,POST;/rest/api/3/status=GET"
)

for entry in "${PROVIDERS[@]}"; do
    IFS='|' read -r name spec filters extra <<<"$entry"
    extra="${extra:-}"
    spec_path="${SPEC_DIR}/${spec}"
    if [[ ! -f "$spec_path" ]]; then
        echo "missing spec $spec_path" >&2
        exit 1
    fi

    filtered="${TEMP_ROOT}/${name}-filtered.json"
    python3 - "$spec_path" "$filtered" "$filters" <<'PYEOF'
import json, sys
src, dst, filters = sys.argv[1], sys.argv[2], sys.argv[3]
if src.endswith(('.yaml', '.yml')):
    import yaml
    with open(src) as f:
        spec = yaml.safe_load(f)
else:
    with open(src) as f:
        spec = json.load(f)
wanted = {}
for part in filters.split(';'):
    if not part.strip():
        continue
    path, methods = part.split('=')
    wanted[path] = {m.strip().lower() for m in methods.split(',')}
out_paths = {}
for path, ops in spec.get('paths', {}).items():
    if path not in wanted:
        continue
    entry = {}
    for key, value in ops.items():
        if key == 'parameters':
            entry[key] = value
        elif key.lower() in wanted[path]:
            entry[key] = value
    if entry:
        out_paths[path] = entry
spec['paths'] = out_paths

def sanitize(obj):
    if isinstance(obj, dict):
        if '$ref' in obj and isinstance(obj['$ref'], str) and obj['$ref'].startswith('#/paths/'):
            return {'type': 'object', 'additionalProperties': True}
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    return obj
spec = sanitize(spec)

with open(dst, 'w') as f:
    json.dump(spec, f)
PYEOF

    out="${TEMP_ROOT}/${name}"
    mkdir -p "$out"
    if [[ -n "$extra" ]]; then
        ADDITIONAL="packageName=pm_gateway.generated.providers.${name},library=httpx,${extra}"
    else
        ADDITIONAL="packageName=pm_gateway.generated.providers.${name},library=httpx"
    fi
    openapi-generator-cli generate \
        -i "$filtered" \
        -g python \
        -o "$out" \
        --skip-validate-spec \
        --additional-properties="$ADDITIONAL" \
        --global-property="apiTests=false,modelTests=false" >/dev/null

    generated="${out}/pm_gateway/generated/providers/${name}"
    if [[ ! -d "$generated" ]]; then
        echo "generated package missing for $name" >&2
        exit 1
    fi
    mkdir -p "${OUTPUT_DIR}"
    rm -rf "${OUTPUT_DIR}/${name}"
    cp -R "$generated" "${OUTPUT_DIR}/${name}"
done

mkdir -p "${PACKAGE_DIR}/src/pm_gateway/generated/providers"
touch "${PACKAGE_DIR}/src/pm_gateway/generated/__init__.py"
touch "${PACKAGE_DIR}/src/pm_gateway/generated/providers/__init__.py"

echo "Generated provider clients in ${OUTPUT_DIR}"
