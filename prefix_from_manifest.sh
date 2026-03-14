#!/usr/bin/env bash
# prefix_from_manifest.sh
#
# Reads order.txt and renames every listed image file by prepending a
# 3-digit sequence number and underscore, so that alphabetical (filename)
# sorting matches the order defined in the manifest.
#
# Example:
#   beach.jpg   → 001_beach.jpg
#   sunset.jpg  → 002_sunset.jpg
#   family.jpg  → 003_family.jpg
#
# Usage:
#   ./prefix_from_manifest.sh <folder>
#
# The folder must contain order.txt.
# After renaming, order.txt is updated to reflect the new filenames.
#
# Safety:
#   - Dry-run mode by default: pass --apply to actually rename files.
#   - Skips blank lines and comment lines (starting with #) in order.txt.
#   - Warns and skips if a source file does not exist.
#   - Refuses to overwrite an existing file with a different name.

set -euo pipefail

# ── Parse arguments ───────────────────────────────────────────────────────────

APPLY=false
FOLDER=""

for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=true ;;
        -*) echo "Unknown option: $arg"; exit 1 ;;
        *)  FOLDER="$arg" ;;
    esac
done

if [[ -z "$FOLDER" ]]; then
    echo "Usage: $0 <folder> [--apply]"
    echo "  Default is dry-run.  Pass --apply to rename files for real."
    exit 1
fi

if [[ ! -d "$FOLDER" ]]; then
    echo "Error: not a directory: $FOLDER"
    exit 1
fi

MANIFEST="$FOLDER/order.txt"
if [[ ! -f "$MANIFEST" ]]; then
    echo "Error: order.txt not found in $FOLDER"
    exit 1
fi

# ── Read manifest, skip comments and blanks ───────────────────────────────────

mapfile -t ALL_LINES < "$MANIFEST"

ENTRIES=()
for line in "${ALL_LINES[@]}"; do
    trimmed="${line#"${line%%[![:space:]]*}"}"   # ltrim
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"  # rtrim
    [[ -z "$trimmed" || "$trimmed" == \#* ]] && continue
    ENTRIES+=("$trimmed")
done

if [[ ${#ENTRIES[@]} -eq 0 ]]; then
    echo "Error: order.txt contains no valid image entries."
    exit 1
fi

echo "Found ${#ENTRIES[@]} entries in order.txt"
if [[ "$APPLY" == false ]]; then
    echo "DRY RUN — pass --apply to rename files for real."
    echo ""
fi

# ── Build rename plan ─────────────────────────────────────────────────────────

ERRORS=0
declare -A RENAME_MAP   # old_name → new_name

IDX=1
for name in "${ENTRIES[@]}"; do
    src="$FOLDER/$name"

    # Check source exists
    if [[ ! -f "$src" ]]; then
        echo "  WARN  file not found, skipping: $name"
        (( ERRORS++ )) || true
        continue
    fi

    # Skip files that already have a 3-digit prefix (NNN_...)
    if [[ "$name" =~ ^[0-9]{3}_ ]]; then
        # Strip existing prefix before adding new one
        bare="${name:4}"
    else
        bare="$name"
    fi

    prefix=$(printf "%03d" "$IDX")
    new_name="${prefix}_${bare}"
    new_path="$FOLDER/$new_name"

    # Refuse to overwrite a different existing file
    if [[ -f "$new_path" && "$new_path" != "$src" ]]; then
        echo "  ERROR target already exists: $new_name  (would overwrite different file)"
        (( ERRORS++ )) || true
        continue
    fi

    RENAME_MAP["$name"]="$new_name"
    echo "  $([ "$APPLY" == true ] && echo "RENAME" || echo "PLAN  ")  $name  →  $new_name"

    (( IDX++ )) || true
done

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo "Aborting: $ERRORS error(s) found above. Fix them before re-running."
    exit 1
fi

# ── Apply renames ─────────────────────────────────────────────────────────────

if [[ "$APPLY" == true ]]; then
    echo ""
    # Two-phase rename via temp names to avoid collisions
    # (e.g. if 002_x.jpg would overwrite the current 001_x.jpg)
    TMPEXT=".renametmp_$$"

    # Phase 1: rename all to temp names
    for old_name in "${!RENAME_MAP[@]}"; do
        mv "$FOLDER/$old_name" "$FOLDER/${RENAME_MAP[$old_name]}${TMPEXT}"
    done

    # Phase 2: strip temp extension
    for old_name in "${!RENAME_MAP[@]}"; do
        mv "$FOLDER/${RENAME_MAP[$old_name]}${TMPEXT}" "$FOLDER/${RENAME_MAP[$old_name]}"
    done

    # Update order.txt to reflect new filenames
    NEW_MANIFEST="$FOLDER/order.txt.new"
    while IFS= read -r line; do
        trimmed="${line#"${line%%[![:space:]]*}"}"
        if [[ -z "$trimmed" || "$trimmed" == \#* ]]; then
            echo "$line"
        else
            bare="$trimmed"
            [[ "$bare" =~ ^[0-9]{3}_ ]] && bare="${bare:4}"
            if [[ -v "RENAME_MAP[$trimmed]" ]]; then
                echo "${RENAME_MAP[$trimmed]}"
            else
                echo "$line"
            fi
        fi
    done < "$MANIFEST" > "$NEW_MANIFEST"

    mv "$NEW_MANIFEST" "$MANIFEST"

    echo "Done. Files renamed and order.txt updated."
else
    echo ""
    echo "Dry run complete. Re-run with --apply to perform the renames."
fi
