#!/bin/bash
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
#   ./prefix_from_manifest.sh <folder> [--apply]
#
# Default is dry-run. Pass --apply to actually rename files.
# After renaming, order.txt is updated to reflect the new filenames.
#
# Compatible with bash 3.2 (macOS default shell).

set -eu

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

if [ -z "$FOLDER" ]; then
    echo "Usage: $0 <folder> [--apply]"
    echo "  Default is dry-run. Pass --apply to rename files for real."
    exit 1
fi

if [ ! -d "$FOLDER" ]; then
    echo "Error: not a directory: $FOLDER"
    exit 1
fi

MANIFEST="$FOLDER/order.txt"
if [ ! -f "$MANIFEST" ]; then
    echo "Error: order.txt not found in $FOLDER"
    exit 1
fi

# ── Read manifest into parallel arrays (bash 3.2 compatible) ─────────────────
# Associative arrays require bash 4+, so we use two indexed arrays:
#   OLD_NAMES[i]  — original filename as listed in order.txt
#   NEW_NAMES[i]  — target filename with NNN_ prefix

OLD_NAMES=()
NEW_NAMES=()
ERRORS=0
IDX=1

while IFS= read -r line; do
    # Strip leading/trailing whitespace
    trimmed=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    # Skip blank lines and comments
    case "$trimmed" in
        ''|\#*) continue ;;
    esac

    src="$FOLDER/$trimmed"

    # Check source file exists
    if [ ! -f "$src" ]; then
        echo "  WARN  file not found, skipping: $trimmed"
        ERRORS=$((ERRORS + 1))
        continue
    fi

    # Strip existing NNN_ prefix if already prefixed, to avoid doubling
    bare=$(echo "$trimmed" | sed 's/^[0-9][0-9][0-9]_//')

    prefix=$(printf "%03d" "$IDX")
    new_name="${prefix}_${bare}"
    new_path="$FOLDER/$new_name"

    # Refuse to overwrite a different existing file
    if [ -f "$new_path" ] && [ "$new_path" != "$src" ]; then
        echo "  ERROR target already exists: $new_name  (would overwrite different file)"
        ERRORS=$((ERRORS + 1))
        continue
    fi

    OLD_NAMES+=("$trimmed")
    NEW_NAMES+=("$new_name")

    if [ "$APPLY" = true ]; then
        echo "  RENAME  $trimmed  →  $new_name"
    else
        echo "  PLAN    $trimmed  →  $new_name"
    fi

    IDX=$((IDX + 1))

done < "$MANIFEST"

echo ""
echo "Planned: ${#OLD_NAMES[@]} renames  |  Skipped/errors: $ERRORS"

if [ $ERRORS -gt 0 ]; then
    echo "Aborting: fix errors above before re-running."
    exit 1
fi

if [ ${#OLD_NAMES[@]} -eq 0 ]; then
    echo "Nothing to rename."
    exit 0
fi

# ── Apply renames ─────────────────────────────────────────────────────────────

if [ "$APPLY" = true ]; then

    TMPEXT=".renametmp_$$"

    # Phase 1 — rename every file to a temp name to avoid collisions
    # (e.g. 002_x.jpg clashing with the current 001_x.jpg mid-run)
    i=0
    while [ $i -lt ${#OLD_NAMES[@]} ]; do
        mv "$FOLDER/${OLD_NAMES[$i]}" "$FOLDER/${NEW_NAMES[$i]}${TMPEXT}"
        i=$((i + 1))
    done

    # Phase 2 — strip temp extension to reach final names
    i=0
    while [ $i -lt ${#NEW_NAMES[@]} ]; do
        mv "$FOLDER/${NEW_NAMES[$i]}${TMPEXT}" "$FOLDER/${NEW_NAMES[$i]}"
        i=$((i + 1))
    done

    # Update order.txt — rewrite line by line, replacing matched entries
    NEW_MANIFEST="$FOLDER/order.txt.new"
    while IFS= read -r line; do
        trimmed=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

        # Pass through blanks and comments unchanged
        case "$trimmed" in
            ''|\#*)
                echo "$line"
                continue
                ;;
        esac

        # Search parallel arrays for a match
        found=false
        i=0
        while [ $i -lt ${#OLD_NAMES[@]} ]; do
            if [ "${OLD_NAMES[$i]}" = "$trimmed" ]; then
                echo "${NEW_NAMES[$i]}"
                found=true
                break
            fi
            i=$((i + 1))
        done

        # No match (e.g. a skipped/errored entry) — leave line as-is
        if [ "$found" = false ]; then
            echo "$line"
        fi

    done < "$MANIFEST" > "$NEW_MANIFEST"

    mv "$NEW_MANIFEST" "$MANIFEST"

    echo "Done. Files renamed and order.txt updated."

else
    echo "Dry run complete. Re-run with --apply to perform the renames."
fi
