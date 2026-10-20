#!/bin/sh
# Runs inside the chroot. Security and stable updates install themselves every day, for the whole life
# of the release (Debian security support, then Debian LTS through the same trixie-security suite).
set -eu

cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

cat > /etc/apt/apt.conf.d/52cherry-unattended-upgrades <<'EOF'
// Cherry OS: install security fixes and Debian's stable point-release updates automatically.
Unattended-Upgrade::Origins-Pattern {
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
        "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
        "origin=Debian,codename=${distro_codename}-updates";
        "origin=Debian,codename=${distro_codename},label=Debian";
        "origin=Cherry OS,codename=${distro_codename}";
        // Nvidia's driver, on computers with an Nvidia card (hooks/35-nvidia.sh)
        "origin=NVIDIA,label=NVIDIA CUDA";
};
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-New-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
// Don't interrupt someone using a laptop on battery.
Unattended-Upgrade::OnlyOnACPower "true";
EOF

# Backports (newer kernels and drivers for new hardware) are available but never installed unasked.
cat > /etc/apt/preferences.d/cherry-backports <<'EOF'
Package: *
Pin: release n=trixie-backports
Pin-Priority: 100
EOF

systemctl enable unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
