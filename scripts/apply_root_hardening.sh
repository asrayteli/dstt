#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

project_root=${DSTT_PROJECT_ROOT:-/home/asray/tools/dstt}
env_file=${DSTT_ENV_FILE:-/home/asray/tools/dsttenv/mail.env}
service_override=/etc/systemd/system/dstt.service.d/override.conf
hardening_dropin=/etc/systemd/system/dstt.service.d/20-hardening.conf
nginx_site=/etc/nginx/sites-available/dstt
nginx_enabled=/etc/nginx/sites-enabled/dstt
stamp=$(date +%Y%m%d-%H%M%S)
backup_dir="/root/dstt-hardening-backup-${stamp}"
mutation_started=false

for required in \
    "${project_root}/deploy/systemd/dstt-hardening.conf" \
    "${project_root}/deploy/nginx/dstt.conf" \
    "${env_file}" \
    "${service_override}" \
    "${nginx_site}"; do
    if [[ ! -f ${required} ]]; then
        echo "Required file not found: ${required}" >&2
        exit 1
    fi
done

install -d -m 0700 "${backup_dir}"
cp -a "${env_file}" "${backup_dir}/mail.env"
cp -a "${service_override}" "${backup_dir}/override.conf"
cp -a "${nginx_site}" "${backup_dir}/nginx-dstt.conf"
if [[ -f ${hardening_dropin} ]]; then
    cp -a "${hardening_dropin}" "${backup_dir}/20-hardening.conf"
    touch "${backup_dir}/hardening-existed"
fi

rollback() {
    local exit_code=$?
    if [[ ${mutation_started} == true ]]; then
        set +e
        echo "Hardening failed; restoring files from ${backup_dir}." >&2
        cp -a "${backup_dir}/mail.env" "${env_file}"
        cp -a "${backup_dir}/override.conf" "${service_override}"
        cp -a "${backup_dir}/nginx-dstt.conf" "${nginx_site}"
        if [[ -f ${backup_dir}/hardening-existed ]]; then
            cp -a "${backup_dir}/20-hardening.conf" "${hardening_dropin}"
        else
            rm -f "${hardening_dropin}"
        fi
        systemctl daemon-reload
        systemctl restart dstt
        if nginx -t; then
            systemctl reload nginx
        fi
    fi
    exit "${exit_code}"
}
trap rollback ERR

extract_override_value() {
    local key=$1
    local value
    value=$(sed -n "s/^Environment=\"\{0,1\}${key}=\(.*\)\"\{0,1\}$/\1/p" "${service_override}")
    value=${value%\"}
    if [[ -z ${value} || ${value} == *$'\n'* ]]; then
        echo "Could not safely extract ${key} from ${service_override}." >&2
        return 1
    fi
    printf '%s' "${value}"
}

read_env_value() {
    local key=$1
    local value
    value=$(sed -n "s/^${key}=//p" "${env_file}")
    if [[ -z ${value} || ${value} == *$'\n'* ]]; then
        echo "Could not safely read ${key} from ${env_file}." >&2
        return 1
    fi
    printf '%s' "${value}"
}

secret_value() {
    local key=$1
    if grep -q "^Environment=\"\{0,1\}${key}=" "${service_override}"; then
        extract_override_value "${key}"
    else
        read_env_value "${key}"
    fi
}

upsert_env() {
    local key=$1
    local value=$2
    local temporary
    temporary=$(mktemp "${env_file}.XXXXXX")
    grep -v "^${key}=" "${env_file}" > "${temporary}" || true
    printf '%s=%s\n' "${key}" "${value}" >> "${temporary}"
    chown --reference="${env_file}" "${temporary}"
    chmod 0600 "${temporary}"
    mv -f "${temporary}" "${env_file}"
}

google_client_id=$(secret_value DSTT_GOOGLE_CLIENT_ID)
google_client_secret=$(secret_value DSTT_GOOGLE_CLIENT_SECRET)

mutation_started=true
upsert_env DSTT_GOOGLE_CLIENT_ID "${google_client_id}"
upsert_env DSTT_GOOGLE_CLIENT_SECRET "${google_client_secret}"

sed -i \
    -e '/^Environment="\{0,1\}DSTT_GOOGLE_CLIENT_ID=/d' \
    -e '/^Environment="\{0,1\}DSTT_GOOGLE_CLIENT_SECRET=/d' \
    "${service_override}"
chmod 0644 "${service_override}"

install -o root -g root -m 0644 \
    "${project_root}/deploy/systemd/dstt-hardening.conf" \
    "${hardening_dropin}"
install -o root -g root -m 0644 \
    "${project_root}/deploy/nginx/dstt.conf" \
    "${nginx_site}"
ln -sfn "${nginx_site}" "${nginx_enabled}"

nginx -t
systemctl daemon-reload
systemctl restart dstt

for _ in $(seq 1 30); do
    if systemctl is-active --quiet dstt && \
       curl --silent --fail \
           -H 'Host: dstt.dipalette.com' \
           http://127.0.0.1:5000/health/ready >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
systemctl is-active --quiet dstt
curl --silent --show-error --fail \
    -H 'Host: dstt.dipalette.com' \
    http://127.0.0.1:5000/health/ready >/dev/null

systemctl reload nginx
curl --silent --show-error --fail --insecure \
    https://dstt.dipalette.com/health/ready >/dev/null

systemctl disable --now gunicorn.service 2>/dev/null || true
systemctl reset-failed gunicorn.service 2>/dev/null || true

trap - ERR
echo "DSTT root hardening completed. Backup: ${backup_dir}"
systemctl --no-pager --full status dstt | sed -n '1,12p'
nginx -t
systemd-analyze security --no-pager dstt.service | tail -n 5
