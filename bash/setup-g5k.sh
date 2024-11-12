#!/bin/bash
sudo-g5k apt install -y stress-ng sysfsutils qemu-kvm virtinst libvirt-clients bridge-utils libvirt-daemon-system
echo "mode class/powercap/intel-rapl:0/energy_uj = 0444" | sudo-g5k tee --append /etc/sysfs.conf
sudo-g5k chmod -R a+r /sys/class/powercap/intel-rapl
sudo-g5k addgroup "$(whoami)" libvirt
sudo-g5k addgroup "$(whoami)" kvm
virsh --connect=qemu:///system net-define /usr/share/libvirt/networks/default.xml
virsh --connect=qemu:///system net-autostart default
virsh --connect=qemu:///system net-start default