#!/bin/sh
set -eu

version="8.30.1"
case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    archive_name="gitleaks_${version}_darwin_arm64.tar.gz"
    expected_sha="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"
    ;;
  Linux:x86_64)
    archive_name="gitleaks_${version}_linux_x64.tar.gz"
    expected_sha="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
    ;;
  *)
    echo "Unsupported platform for pinned Gitleaks: $(uname -s) $(uname -m)" >&2
    exit 1
    ;;
esac

cache_dir=".cache/tools/gitleaks/${version}"
binary="${cache_dir}/gitleaks"
archive="${cache_dir}/${archive_name}"

if [ ! -x "$binary" ]; then
  mkdir -p "$cache_dir"
  curl --fail --location --silent --show-error \
    "https://github.com/gitleaks/gitleaks/releases/download/v${version}/${archive_name}" \
    --output "$archive"
  printf "%s  %s\n" "$expected_sha" "$archive" | shasum -a 256 --check
  tar -xzf "$archive" -C "$cache_dir" gitleaks
  chmod +x "$binary"
fi

"$binary" detect --config .gitleaks.toml --no-git
