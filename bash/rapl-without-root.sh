#!/bin/bash
sudo-g5k apt install sysfsutils
echo "mode class/powercap/intel-rapl:0/energy_uj = 0444" | sudo-g5k tee --append /etc/sysfs.conf
sudo-g5k chmod -R a+r /sys/class/powercap/intel-raplsudo chmod -R a+r /sys/class/powercap/intel-rapl