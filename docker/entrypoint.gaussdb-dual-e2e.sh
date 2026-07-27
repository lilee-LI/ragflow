#!/usr/bin/env bash
set -e

runtime_root=/tmp/gaussdb-dual-e2e-runtime
conf_root=/tmp/gaussdb-dual-e2e-conf
patched_entrypoint=/tmp/ragflow-entrypoint-gaussdb-dual-e2e.sh

rm -rf "$runtime_root" "$conf_root"
mkdir -p "$runtime_root" "$conf_root"
cp -a /e2e-code/conf/. "$conf_root/"
cp /e2e-code/docker/service_conf.yaml.template "$conf_root/service_conf.yaml.template"

sed \
  -e "s|CONF_DIR=\"/ragflow/conf\"|CONF_DIR=\"$conf_root\"|" \
  -e 's|export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu/"|export LD_LIBRARY_PATH="/opt/gaussdb-python/lib:/usr/lib/x86_64-linux-gnu/"|' \
  -e 's#LD_PRELOAD="$JEMALLOC_PATH"#LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libstdc++.so.6:$JEMALLOC_PATH"#' \
  -e 's#^ensure_db_init$#[[ "${GAUSSDB_E2E_SKIP_DB_INIT:-0}" == "1" ]] || ensure_db_init#' \
  /ragflow/entrypoint.base.sh > "$patched_entrypoint"
chmod +x "$patched_entrypoint"

for path in admin agent api common deepdoc memory mcp ragflow_deps tools; do
  cp -a "/e2e-code/$path" "$runtime_root/$path"
done
mkdir -p "$runtime_root/rag/res"
for path in /ragflow/rag/res/*; do
  ln -s "$path" "$runtime_root/rag/res/$(basename "$path")"
done
for path in /e2e-code/rag/res/*; do
  name="$(basename "$path")"
  rm -rf "$runtime_root/rag/res/$name"
  cp -a "$path" "$runtime_root/rag/res/$name"
done
for path in /e2e-code/rag/*; do
  name="$(basename "$path")"
  [[ "$name" == "res" ]] && continue
  cp -a "$path" "$runtime_root/rag/$name"
done
ln -s "$conf_root" "$runtime_root/conf"
cp /e2e-code/pyproject.toml "$runtime_root/pyproject.toml"

export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libgcc_s.so.1:/usr/lib/x86_64-linux-gnu/libstdc++.so.6"
cd "$runtime_root"
exec "$patched_entrypoint" "$@"
