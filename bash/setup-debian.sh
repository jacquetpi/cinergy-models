#!/bin/bash
sudo apt install -y stress-ng sysfsutils qemu-kvm virtinst libvirt-clients bridge-utils libvirt-daemon-system
echo "mode class/powercap/intel-rapl:0/energy_uj = 0444" | sudo tee --append /etc/sysfs.conf
sudo chmod -R a+r /sys/class/powercap/intel-rapl
sudo /usr/sbin/adduser "$(whoami)" libvirt
sudo /usr/sbin/adduser "$(whoami)" kvm
virsh --connect=qemu:///system net-define /usr/share/libvirt/networks/default.xml
virsh --connect=qemu:///system net-autostart default
virsh --connect=qemu:///system net-start default