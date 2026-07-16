#!/bin/sh
set -eu

version="8.30.1"
case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    archive_name="gitleaks_${version}_darwin_arm64.tar.gz"
    expected_sha="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"
    expected_binary_sha="ba52fb1bfabbcde42f032afad3d6e0b19dff8ed105229a16e7caa338bbc0e84f"
    ;;
  Linux:x86_64)
    archive_name="gitleaks_${version}_linux_x64.tar.gz"
    expected_sha="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
    expected_binary_sha="88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509"
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

printf "%s  %s\n" "$expected_binary_sha" "$binary" | shasum -a 256 --check
"$binary" dir --config .gitleaks-dir.toml --redact --verbose .
"$binary" git --config .gitleaks.toml --redact --verbose .
