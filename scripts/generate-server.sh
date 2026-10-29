#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SPEC_PATH="${PACKAGE_DIR}/spec/pm-api-openapi-v0.1.yaml"
OUTPUT_GENERATED_DIR="${PACKAGE_DIR}/src/pm_gateway/generated"
GENERATOR_CLI="${OPENAPI_GENERATOR_CLI:-openapi-generator-cli}"
GENERATOR_VERSION="${OPENAPI_GENERATOR_VERSION:-7.24.0}"
CHECK_ONLY=0

usage() {
    cat <<EOF
Usage: $0 [--check] [--spec PATH] [--package-dir GENERATED_DIR]

Generate the PM gateway's FastAPI server routes and models from the OpenAPI
contract. Generated output is limited to pm_gateway/generated/apis,
pm_gateway/generated/models, and pm_gateway/generated/security_api.py;
handwritten implementation modules are kept. With --check, compare those
outputs without modifying the package.

Environment:
  OPENAPI_GENERATOR_CLI       CLI executable (default: openapi-generator-cli)
  OPENAPI_GENERATOR_VERSION   Required CLI version (default: 7.24.0)
EOF
}

while (($#)); do
    case "$1" in
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --spec)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            SPEC_PATH="$2"
            shift 2
            ;;
        --package-dir)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            OUTPUT_GENERATED_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "$SPEC_PATH" ]]; then
    printf 'OpenAPI specification not found: %s\n' "$SPEC_PATH" >&2
    exit 1
fi

if ! command -v "$GENERATOR_CLI" >/dev/null 2>&1; then
    printf 'OpenAPI Generator CLI not found: %s\n' "$GENERATOR_CLI" >&2
    printf 'Install OpenAPI Generator %s and Java, or set OPENAPI_GENERATOR_CLI.\n' "$GENERATOR_VERSION" >&2
    exit 1
fi

installed_version="$("$GENERATOR_CLI" version | tr -d '\r' | tail -n 1)"
if [[ "$installed_version" != "$GENERATOR_VERSION" ]]; then
    printf 'OpenAPI Generator version mismatch: expected %s, found %s\n' \
        "$GENERATOR_VERSION" "$installed_version" >&2
    printf 'Set OPENAPI_GENERATOR_VERSION to deliberately use another version.\n' >&2
    exit 1
fi

TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pm-gateway-openapi.XXXXXX")"
trap 'rm -rf -- "$TEMP_ROOT"' EXIT

PACKAGE_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${PACKAGE_DIR}/src/pm_gateway/_version.py")"
if [[ -z "$PACKAGE_VERSION" ]]; then
    printf 'Could not read the package version from %s\n' \
        "${PACKAGE_DIR}/src/pm_gateway/_version.py" >&2
    exit 1
fi

"$GENERATOR_CLI" generate \
    -i "$SPEC_PATH" \
    -g python-fastapi \
    -o "$TEMP_ROOT/generated" \
    --additional-properties="packageName=pm_gateway,fastapiImplementationPackage=impl,packageVersion=${PACKAGE_VERSION},serverPort=8080,disallowAdditionalPropertiesIfNotPresent=false" \
    --global-property="apiTests=false,modelTests=false"

generated_package="${TEMP_ROOT}/generated/src/pm_gateway"
generated_server_dir="${TEMP_ROOT}/generated-server"
for required_path in "${generated_package}/apis" "${generated_package}/models" "${generated_package}/security_api.py"; do
    if [[ ! -e "$required_path" ]]; then
        printf 'Expected generated output is missing: %s\n' "$required_path" >&2
        exit 1
    fi
done

mkdir -p "$generated_server_dir"
for generated_path in apis models security_api.py; do
    cp -R -- "${generated_package}/${generated_path}" "$generated_server_dir/"
done

# Keep generated code in its own package namespace while preserving the
# handwritten implementation package used by generated route dispatch.
find "${generated_server_dir}/apis" "${generated_server_dir}/models" -type f -name '*.py' -exec \
    sed -i -E 's/pm_gateway\.(apis|models|security_api)/pm_gateway.generated.\1/g' {} +
sed -i -E 's/pm_gateway\.(apis|models|security_api)/pm_gateway.generated.\1/g' \
    "${generated_server_dir}/security_api.py"

for required_path in "${generated_server_dir}/apis" "${generated_server_dir}/models" "${generated_server_dir}/security_api.py"; do
    if [[ ! -e "$required_path" ]]; then
        printf 'Expected generated output is missing: %s\n' "$required_path" >&2
        exit 1
    fi
done

# Query parameters are received as strings by HTTP. OpenAPI Generator marks
# bounded integer query parameters as strict, which makes FastAPI reject valid
# values such as `?limit=200` before the route runs. Keep their bounds while
# allowing FastAPI to parse the query string into an integer.
find "${generated_server_dir}/apis" -type f -name '*.py' -exec \
    sed -i 's/Field(le=200, strict=True, ge=1)/Field(le=200, ge=1)/g' {} +

# OpenAPI Generator's model `from_dict` helpers pass `obj.get(key)` for every
# schema property into model_validate, which marks absent fields as explicitly
# set. For merge-patch request models this corrupts RFC 7386 semantics: an
# omitted field becomes indistinguishable from an explicit null, and a
# passthrough consumer would clear fields the caller never provided. Validate
# the raw payload instead; aliases and nested model coercion still apply while
# absent fields stay unset.
python3 - "${generated_server_dir}/models/work_item_patch.py" \
    "${generated_server_dir}/models/transition_request.py" <<'PYEOF'
import re
import sys

for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    patched, count = re.subn(
        r"_obj = cls\.model_validate\(\{.*?\n        \}\)",
        "# Keep only the keys present in the payload: model_validate marks\n"
        "        # absent fields as unset, preserving RFC 7386 merge-patch\n"
        "        # semantics for omitted fields versus explicit nulls.\n"
        "        _obj = cls.model_validate(obj)",
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        sys.exit(f"expected exactly one from_dict payload mapping in {path}, found {count}")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(patched)
PYEOF

if ((CHECK_ONLY)); then
    for current_path in "${OUTPUT_GENERATED_DIR}/apis" "${OUTPUT_GENERATED_DIR}/models" "${OUTPUT_GENERATED_DIR}/security_api.py"; do
        if [[ ! -e "$current_path" ]]; then
        printf 'Generated output is missing from the package: %s\n' "$current_path" >&2
            exit 1
        fi
    done

    diff_status=0
    diff -ru --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' -- \
        "${OUTPUT_GENERATED_DIR}/apis" "${generated_server_dir}/apis" || {
        status=$?
        ((status > diff_status)) && diff_status=$status
    }
    diff -ru --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' -- \
        "${OUTPUT_GENERATED_DIR}/models" "${generated_server_dir}/models" || {
        status=$?
        ((status > diff_status)) && diff_status=$status
    }
    diff -u -- "${OUTPUT_GENERATED_DIR}/security_api.py" "${generated_server_dir}/security_api.py" || {
        status=$?
        ((status > diff_status)) && diff_status=$status
    }
    if ((diff_status != 0)); then
        if ((diff_status == 1)); then
            printf 'Generated server code is out of date. Run %s to update it.\n' "$0" >&2
        else
            printf 'Could not compare generated server code (diff exit %s).\n' "$diff_status" >&2
        fi
        exit "$diff_status"
    fi

    printf 'Generated server code is up to date in %s\n' "$OUTPUT_GENERATED_DIR"
    exit 0
fi

mkdir -p "$OUTPUT_GENERATED_DIR"
rm -rf -- "${OUTPUT_GENERATED_DIR}/apis" "${OUTPUT_GENERATED_DIR}/models"
rm -f -- "${OUTPUT_GENERATED_DIR}/security_api.py"
cp -R -- "${generated_server_dir}/apis" "${OUTPUT_GENERATED_DIR}/apis"
cp -R -- "${generated_server_dir}/models" "${OUTPUT_GENERATED_DIR}/models"
cp -- "${generated_server_dir}/security_api.py" "${OUTPUT_GENERATED_DIR}/security_api.py"

printf 'Generated PM gateway server code in %s\n' "$OUTPUT_GENERATED_DIR"
