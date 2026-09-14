# shellcheck shell=sh
# Shipped by the bundle-from-dir recipe. /etc/profile.d/*.sh is SOURCED by both flavours'
# /etc/profile, never executed, so a shebang here would be a misleading no-op. The
# directive above says what shell to lint against without pretending to be one.
export PATH="/usr/local/bin:$PATH"
