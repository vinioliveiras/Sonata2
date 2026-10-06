#!/usr/bin/env bash
# Install Sonata from scratch in a clean distro (a Docker container), as a new
# user would: a normal account with sudo, ./install.sh --yes, then `sonata2
# doctor`. For releases (Vini: a friend's install failed).
#
#   tools/test-install.sh                 # arch (CachyOS, EndeavourOS... are Arch-based)
#   tools/test-install.sh fedora debian ubuntu
#   CA=/path/ca.crt tools/test-install.sh # a proxy's CA, trusted inside the container
#
# Logs: /tmp/sonata-test-install/<distro>.log. Exit 1 if any install failed.
# Not tested here: the session itself (no display, no systemd in a container).
set -uo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-/tmp/sonata-test-install}"
mkdir -p "$OUT"
image_of() {
    case "$1" in
        arch) echo archlinux:latest ;; fedora) echo fedora:latest ;;
        debian) echo debian:trixie ;; ubuntu) echo ubuntu:25.04 ;;
        *) echo "$1" ;;
    esac
}
prepare_of() {   # root: the CA, sudo, a user "friend" with sudo
    local ca='[ -f /ca.crt ] && { cp /ca.crt /usr/local/share/ca-certificates/proxy.crt 2>/dev/null; cp /ca.crt /etc/pki/ca-trust/source/anchors/ 2>/dev/null; cp /ca.crt /etc/ca-certificates/trust-source/anchors/ 2>/dev/null; update-ca-certificates 2>/dev/null; update-ca-trust 2>/dev/null; true; }'
    case "$1" in
        arch) echo "$ca; sed -i 's/^CheckSpace/#CheckSpace/' /etc/pacman.conf && pacman -Syu --noconfirm sudo git base-devel" ;;
        fedora) echo "$ca; dnf -y install sudo git" ;;
        debian|ubuntu) echo "$ca; apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y sudo git ca-certificates" ;;
    esac
}
fail=0
for d in "${@:-arch}"; do
    img="$(image_of "$d")"
    tag="sonata-test-$d"
    ctx="$(mktemp -d)"
    tar -C "$SRC" --exclude=.git --exclude=__pycache__ -cf - . | (mkdir -p "$ctx/src" && tar -C "$ctx/src" -xf -)
    if [ -n "${CA:-}" ]; then cp "$CA" "$ctx/ca.crt"; else : > "$ctx/ca.crt"; fi
    cat > "$ctx/Dockerfile" <<EOF
FROM $img
COPY ca.crt /ca.crt
RUN sh -c "$(prepare_of "$d")" && useradd -m -s /bin/bash friend && \
    echo 'friend ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/friend
COPY --chown=friend:friend src /home/friend/sonata2
USER friend
WORKDIR /home/friend/sonata2
EOF
    echo "== $d ($img)"
    if ! DOCKER_BUILDKIT=0 docker build -q -t "$tag" "$ctx" > "$OUT/$d.build.log" 2>&1; then
        echo "   image failed: $OUT/$d.build.log"; fail=1; rm -rf "$ctx"; continue
    fi
    rm -rf "$ctx"
    docker run --rm ${CA:+-e PIP_CERT=/ca.crt -e REQUESTS_CA_BUNDLE=/ca.crt} "$tag" bash -c './install.sh --yes 2>&1; echo "INSTALL EXIT=$?"' > "$OUT/$d.log" 2>&1
    if grep -q "INSTALL EXIT=0" "$OUT/$d.log"; then
        echo "   installed"
    else
        echo "   INSTALL FAILED:"; grep -E "stopped at line|INSTALL EXIT" "$OUT/$d.log" | sed 's/^/     /'; fail=1
    fi
    grep -E "^\[(warn|FAIL)\]" "$OUT/$d.log" | sed 's/^/   /'
    # installed but doctor says it can't run (Fedora: GTK without its cairo typelib)
    grep -q "^\[FAIL\]" "$OUT/$d.log" && { echo "   DOCTOR FAILED"; fail=1; }
done
exit $fail
