#!/bin/bash
# echo -n > /etc/machine-id
# rm /var/lib/dbus/machine-id
# ln -s /etc/machine-id /var/lib/dbus/machine-id
if (( "$#" != "2" )) 
then
  echo -n "Missing argument : ./launchvm.sh host-core estimated-duration"
  exit -1
fi
hostcore="$1"
duration="$2"
echo "launchvm: $hostcore $duration"

vmname="vm1"
vmcpu="4"
vmmem="8192"
pathbase="/var/lib/libvirt/images"

#Setup : clear old data
rm /tmp/vmready-sync
virsh --connect=qemu:///system destroy "$vmname"
virsh --connect=qemu:///system undefine "$vmname"

# Setup : install data
#rsync -avhW --no-compress --progress --info=progress2 "$image" "$pathbase"/"$vmname".qcow2
virt-install --connect qemu:///system --import --name "$vmname" --vcpu "$vmcpu" --memory "$vmmem" --disk ${pathbase}/"$vmname".qcow2,format=qcow2,bus=virtio --import --os-variant ubuntu20.04 --network default --virt-type kvm --noautoconsole --check path_in_use=off

# Post action : wait to retrieve vm ip
while sleep 10;
do
  vmip=$( virsh --connect=qemu:///system domifaddr "$vmname" | tail -n 2 | head -n 1 | awk '{ print $4 }' | sed 's/[/].*//' );
  if [ -n "$vmip" ]; then #VAR is set to a non-empty string
    break
  fi
done
count=0
while true; # May not be fully initialized : test if ssh works (is ping enough?)
do
  ssh_test=$( ssh vm@"${vmip}" -o StrictHostKeyChecking=no 'echo success' )
  if [[ $ssh_test == *"success"* ]]; then
    echo "launchvm: SSH service of $vmname is ready with ip $vmip"
    break
  fi
  count=$(( count + 1 ))
  echo "launchvm: Unable to ssh to $vmname with ip $vmip (trial $count)"
  sleep 5
done

# Post action: Application related
payload="cd ~/src/DeathStarBench/socialNetwork/ && docker-compose up -d && sleep 30 && python3 scripts/init_social_graph.py --graph=socfb-Reed98"
ssh vm@"${vmip}" -o StrictHostKeyChecking=no "$payload"
echo -n "launchvm: Install of $vmname finished, launching workload"

# Now, execute workload
touch /tmp/vmready-sync

requests=(
    2000
    10000
    5000
    100
)
iterationdelay=30
start=$( date +%s )
while true;
do
    for reqs in "${requests[@]}"; do
        current=$( date +%s )
        runtime=$((current-start+iterationdelay))
        if [ "$runtime" -ge "$duration" ]; then
            exceeded=true
            break
        fi
        payload="docker run --rm --net=host pjacquet/dsb-socialnetwork-wrk2 -D exp -t 4 -c 8 -d $iterationdelay -L -s ./scripts/social-network/read-home-timeline.lua http://localhost:8080/wrk2-api/home-timeline/read -R $reqs"
        output=$( ssh vm@"${vmip}" -o StrictHostKeyChecking=no "$payload" )
        # echo -n "$output" > "dump/${fileoutput}"
        # Out of interest for us
    done
    if [ "$exceeded" = true ] ; then
        break
    fi;
done

# Finish, clean up
payload="cd ~/src/DeathStarBench/socialNetwork/ && docker-compose down"
ssh vm@"${vmip}" -o StrictHostKeyChecking=no "$payload"

virsh --connect=qemu:///system shutdown "$vmname"

echo "launchvm: Finish! Exciting"
