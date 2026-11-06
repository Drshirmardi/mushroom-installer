#!/usr/bin/env bash
set -Eeuo pipefail
: "${RELEASE_VERSION:?}" "${GITHUB_SHA:?}" "${GITHUB_REPOSITORY:?}"
[[ $RELEASE_VERSION =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
if existing=$(gh release view "$RELEASE_VERSION" --json isDraft,targetCommitish 2>/dev/null); then
  [[ $(jq -r .targetCommitish <<<"$existing") == "$GITHUB_SHA" ]] || {
    echo 'Release version already belongs to another commit.' >&2; exit 1;
  }
  if [[ $(jq -r .isDraft <<<"$existing") == false ]]; then
    echo 'This exact release is already published; assets were preserved.'
    exit 0
  fi
else
  gh release create "$RELEASE_VERSION" --draft --target "$GITHUB_SHA" \
    --title "Mushroom installer $RELEASE_VERSION" \
    --notes "Verified standalone installer. Source commit: $GITHUB_SHA. Server configuration and secrets are preserved when updating the tool."
fi
gh release upload "$RELEASE_VERSION" --clobber \
  installation/dist/mushroom-installer.tar.gz \
  installation/dist/mushroom-installer.tar.gz.sha256 \
  installation/dist/release.json installation/dist/install.sh
latest=$(gh release view --json tagName --jq .tagName 2>/dev/null || true)
mark_latest=true
if [[ -n $latest && $(printf '%s\n%s\n' "$latest" "$RELEASE_VERSION" | sort -V | tail -1) != "$RELEASE_VERSION" ]]; then
  mark_latest=false
fi
gh release edit "$RELEASE_VERSION" --draft=false --latest="$mark_latest"
