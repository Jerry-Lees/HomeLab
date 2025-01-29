## Introduction

As Networks and Infrastructure have become more complex and
interconnected it has become harder and harder to manually configure
pieces of a companies infrastructure in a consistently correct manner.
The fact is-- Mistakes are always made by humans.

In the early days, we did this with scripting or even farther back
notepooks filled with notes. The tools have changed and the processed
refined but the concepts are the same-- break the task down into bite
sized chunks and do them repeatedly. This document attempts to do just
that; break down the task of building a Lab BIG-IP device into usable
(and reusable) pieces.

## Prepare The Environment

Each time you run through this process, you will need to do the
following. This process can be 100% automated, but for this process we
will work through some items that could be automated manually to
re-enforce the steps to be completed.

### Prepare your system

Through multiple runs, you'll need to prep the systems you are
connecting with. Run the following after the first run, and every run
after:

`ssh-keygen -f '/home/yourusernamehere/.ssh/known_hosts' -R '$IP1'`  
`ssh-keygen -f '/home/yourusernamehere/.ssh/known_hosts' -R '$IP2'`

### Preparing to use the build process

You will need to do the following to the files in the repository to make
them run:

1.  update the clustered01.json to contain your new system's reg key and
    any environment specific information; hostnames, ips, vlans, etc
2.  update the clustered02.json to contain your new system's reg key and
    any environment specific information; hostnames, ips, vlans, etc
3.  update the lab-as3.json with your test virtual server, pool members,
    and IP addresses for your environment.
4.  download the AS3 and DO RPMS into the repository folder "\[Your
    projects folder\]/HomeLab/F5/BIG-IP Classic"
5.  Download and place the BIG-IP QCOW onto the location that the
    Proxmox can access it-- do this on ALL proxmox devices. I used just
    the home folder root on my proxmox devices, but I also found this
    thread helpful:
    <https://forum.proxmox.com/threads/import-of-qcow2-images-to-proxmox.130562/>
6.  I have used "local-lvm" for the storage location on the imported
    disk, since it is standard and always there by default, you may want
    to use a different location.
7.  the the qm create command I have used "vmbr0" for my bridge name,
    yours may be different and your vlan tags may be different as well.

## Build ProxMox devices

The first task is to create the compute on the hypervisor. We will do
this on Proxmox, since we're targeting a "Home Lab" deployment. Keep in
mind, Proxmox isn't officially supported by F5, so we're on our own for
support-- but that should be fine. The steps will be similar, but
definitely different for other supported hypervisors. Consult those
hypervisors documentation for adapting this step to those hypervisors.

### Requirements

1.  Minimum of two proxmox servers (or other Hypervisors, but commands
    will vary)
2.  SSH access to the proxmox servers
3.  Commands cannot be run concurrently on the two devices since they
    automatically determine the next available ID. (You can, however,
    start the second server once the first has created the VM with
    successful execution of the "qm create" command.)
4.  Keep in mind, the commands will need to be adjusted and changed
    where nessisary to tailor this to your environment.

### Build Process steps

To build the VMs, Run these commands:

`#BIGIP01 on the first proxmox server`  
  
`#Get Next VM ID and setup variables `  
`NextID=$(pvesh get /cluster/nextid)`  
`Name="HOMELAB-01-17.1.2"`  
`Qcow2="BIGIP-17.1.2-0.0.8.qcow2"`  
`#create VM `  
`qm create $NextID --machine pc-i440fx-2.12 --name $Name --memory 16384 --cores 4 --net0 vmxnet3,bridge=vmbr0,tag=100 --net1 vmxnet3,bridge=vmbr0,tag=200 --net2 vmxnet3,bridge=vmbr0,tag=300 --net3 vmxnet3,bridge=vmbr0,tag=220 --net4 vmxnet3,bridge=vmbr1`  
`#import qcow`  
`qm importdisk $NextID $Qcow2 local-lvm`  
`#attach disk to vm`  
`qm set $NextID --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-$NextID-disk-0`  
`#set as bootable`  
`qm set $NextID --boot c --bootdisk scsi0`  
`#start the VM`  
`qm start $NextID`  

1.  BIGIP02 on the second proxmox server

`#Get Next VM ID and setup variables`  
`NextID=$(pvesh get /cluster/nextid)`  
`Name="HOMELAB-02-17.1.2"`  
`Qcow2="BIGIP-17.1.2-0.0.8.qcow2"`  
`#create VM`  
`qm create $NextID --machine pc-i440fx-2.12 --name $Name --memory 16384 --cores 4 --net0 vmxnet3,bridge=vmbr0,tag=100 --net1 vmxnet3,bridge=vmbr0,tag=200 --net2 vmxnet3,bridge=vmbr0,tag=300 --net3 vmxnet3,bridge=vmbr0,tag=220 --net4 vmxnet3,bridge=vmbr1`  
`#import qcow`  
`qm importdisk $NextID $Qcow2 local-lvm`  
`#attach disk to vm`  
`qm set $NextID --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-$NextID-disk-0`  
`#set as bootable`  
`qm set $NextID --boot c --bootdisk scsi0`  
`#start the VM`  
`qm start $NextID`  

**Note: These steps takes around 2-4 minutes on my systems.**

### Base Connectivity

Next we have to get base connectivity created for management of the
devices. Note that this could potentialy be completed with a cloud init,
but that is outside of the scope of this discussion.

After setting the password with the config tool on the BIG-IP, run the
following commands to set the management Address and login to change
admin password and set the setup wizard to not run.

`tmsh modify sys global-settings gui-setup disabled`  
`passwd admin yourpasswordhere`

### F5 Configuration Automation

Next we will use Declarative Onboarding to set all the Base
configuration items, such as; interfaces, hostnames, IP addresses,
vlans, and even device service clustering to setup an HA Pair of BIG-IP
Devices. This information is from
<https://clouddocs.f5.com/products/extensions/f5-declarative-onboarding/latest/installation.html>
Additionally, we will use AS3 to deploy a configuration containing
Virtual Servers, Pools, and pool members. These are not installed by
default, so we must download them and install them. For this
demonstration, it is recommended to download them and save them to the
folder where the declaration files are stored. For production type
deployments this might be a different location.

### Install DO and AS3 RPMs

First we need to install the DO and AS3 RPMs. This will set up the
installation on the remote BIG-IPs, to demonstrate this run the
following commands to show the RPM that is on your device already is not
there:

`ls -l /var/config/rest/downloads`

(you won't see the DO RPM there)

**Run these on your "orchestration" device where you have downloaded the
DO and AS3 declaration files:** ***Note:* This assumes you have the
declaration files and the RPMs are in the same folder and that you are
in that folder at the command line when you run it.**

`# Upload DO`  
`FN=f5-declarative-onboarding-1.46.0-7.noarch.rpm`  
`CREDS=admin:yourpasswordhere`  
`IP1=10.100.100.30`  
`IP2=10.100.100.31`  
`LEN=$(wc -c $FN | cut -f 1 -d ' ')`  
`curl -kvu $CREDS `[https://$IP1/mgmt/shared/file-transfer/uploads/$FN](https://$IP1/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`  
`curl -kvu $CREDS `[https://$IP2/mgmt/shared/file-transfer/uploads/$FN](https://$IP2/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`

`# Upload AS3`  
`FN=f5-appsvcs-3.53.0-7.noarch.rpm`  
`CREDS=admin:yourpasswordhere`  
`IP1=10.100.100.30`  
`IP2=10.100.100.31`  
`LEN=$(wc -c $FN | cut -f 1 -d ' ')`  
`curl -kvu $CREDS `[`https://$IP1/mgmt/shared/file-transfer/uploads/$FN`](https://$IP1/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`  
`curl -kvu $CREDS `[`https://$IP2/mgmt/shared/file-transfer/uploads/$FN`](https://$IP2/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`

**Note: at this point, the files**should be**be on the BIG-IPs at
/var/config/rest/downloads/. On the BIG-IPs, run:**

`ls -l /var/config/rest/downloads`

Next we will perform the installation of each RPM on the BIG-IPs with an
API call via curl.

`#install DO`  
`FN=f5-declarative-onboarding-1.46.0-7.noarch.rpm DATA="{\"operation\":\"INSTALL\",\"packageFilePath\":\"/var/config/rest/downloads/$FN\"}"`  
`curl -kvu $CREDS "`[`https://$IP1/mgmt/shared/iapp/package-management-tasks`](https://$IP1/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP1`](https://$IP1)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`  
`curl -kvu $CREDS "`[`https://$IP2/mgmt/shared/iapp/package-management-tasks`](https://$IP2/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP2`](https://$IP2)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`

`#install AS3`  
`FN=f5-appsvcs-3.53.0-7.noarch.rpm`  
`DATA="{\"operation\":\"INSTALL\",\"packageFilePath\":\"/var/config/rest/downloads/$FN\"}"`  
`curl -kvu $CREDS "`[`https://$IP1/mgmt/shared/iapp/package-management-tasks`](https://$IP1/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP1`](https://$IP1)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`  
`curl -kvu $CREDS "`[`https://$IP2/mgmt/shared/iapp/package-management-tasks`](https://$IP2/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP2`](https://$IP2)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`

This should happen pretty quickly, no more than a minute or two
normally.

**If you need to check on the status of a task, Use these commands to
check the status of the installations. This shouldn't be needed and is
only mentioned to assist in troubleshooting.**

`QUERY="\{ \"operation\": \"QUERY\" \}"`  
`curl -k --location --request GET '`[`https://$IP1/mgmt/shared/iapp/package-management-tasks/`](https://$IP1/mgmt/shared/iapp/package-management-tasks/)`' --header 'Content-Type: application/json' --header 'Authorization: Basic YWRtaW46eW91cnBhc3N3b3JkaGVyZQ==' --data '{ "operation": "QUERY" }'|jq|grep -E 'packageName|status|id|error|startTime|endTime'`  
`curl -k --location --request GET '`[`https://$IP2/mgmt/shared/iapp/package-management-tasks/`](https://$IP2/mgmt/shared/iapp/package-management-tasks/)`' --header 'Content-Type: application/json' --header 'Authorization: Basic YWRtaW46eW91cnBhc3N3b3JkaGVyZQ==' --data '{ "operation": "QUERY" }'|jq|grep -E 'packageName|status|id|error|startTime|endTime'`

## DO Base Config

Once the DO RPMs are installed we can send a DO Declaration to our newly
created and accessible VE guests with the following commands:

`ID1=$(curl -k -X POST -u admin:yourpasswordhere -H "Content-Type: application/json" -d @clustered01.json `[`https://$IP1/mgmt/shared/declarative-onboarding`](https://$IP1/mgmt/shared/declarative-onboarding)` | jq | grep '\"id\"' | awk '{print $2}'|sed 's/[\"|\,| ]//g');echo $ID1`  
`ID2=$(curl -k -X POST -u admin:yourpasswordhere -H "Content-Type: application/json" -d @clustered02.json `[`https://$IP2/mgmt/shared/declarative-onboarding`](https://$IP2/mgmt/shared/declarative-onboarding)` | jq | grep '\"id\"' | awk '{print $2}'|sed 's/[\"|\,| ]//g');echo $ID2`

To be clear, These commands not only send the declaration but parse the
response for the ID of the tasks that are created and saves them into a
variable for later use in this process.

The following commands should give you details on each of the tasks,
should you need them.:

`curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1`](https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1)` | jq | grep -A5 result`  
`curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2`](https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2)` | jq | grep -A5 result`

Or optionally below is a one-liner that accomplishes the same thing:

`curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1`](https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1)` | jq | grep -A15 result && curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2`](https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2)` | jq | grep -A15 result`

For Troubleshooting, If there are errors on one or more boxes, you can
run the commands again-- but without the grep to get the full details:

`curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1`](https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1)` | jq`  
`curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2`](https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2)` | jq`

**Note: This Step may take a few minutes to complete. It takes about
10-15 minutes on my lab systems. During the process you will see
services start and stop at the command line and be logged off the
devices if you are in the GUI.**

## AS3 declare configuration

Next up, once we have a completed HA Pair with a base configuration,
we'll install the shared configuration for Virtual servers and such.

`CREDS=admin:yourpasswordhere`  
`IP1=10.100.100.30`  
`IP2=10.100.100.31`  
`#Note: For your reference, lab-as3.json is a derivative of this example: `[`https://github.com/F5Networks/f5-appsvcs-extension/blob/main/examples/declarations/example-http-https-one-declaration.json`](https://github.com/F5Networks/f5-appsvcs-extension/blob/main/examples/declarations/example-http-https-one-declaration.json)  
`curl -k -X POST -u $CREDS -H "Content-Type: application/json" -d @lab-as3.json `[`https://$IP1/mgmt/shared/appsvcs/declare`](https://$IP1/mgmt/shared/appsvcs/declare)  
`#perform a config sync`  
`curl -sk -u $CREDS -H "Content-Type: application/json" -X POST -d '{"command":"run","utilCmdArgs":"config-sync to-group failoverGroup"}' `[`https://$IP1/mgmt/tm/cm`](https://$IP1/mgmt/tm/cm)

## profit!

At this point, you should have a working BIG-IP HA Pair with a base
configuration and also shared configurations including two virtual
servers inside an administrative partition.

### All The Steps, no (minimal) fluff

The below section has all the same steps as above, but with less
commentary so it can be easily seen.

#### Steps for repeating the process

Initially you won't need to do the first two items on this list, but in
order to repeat the steps again you will need to remove any known keys
for the IPs that you are using which is common to all systems using ssh,
not just BIG-IP, and you will need to revoke the licenses installed
**before** you delete the device-- this is also relevant to deletions if
you a using a BIG-IQ device for licensing. If you do not do this, the
license will not work on the new device and a support call will need to
be initiated.

Bips:

`tmsh revoke sys license`

Local device:

`ssh-keygen -f '/home/yourusernamehere/.ssh/known_hosts' -R '$IP1'`  
`ssh-keygen -f '/home/yourusernamehere/.ssh/known_hosts' -R '$IP2'`

#### "The process"

Proxmox03:

**1.** On the first Proxmox server, run the following:

`#Get Next VM ID and setup variables `  
`NextID=$(pvesh get /cluster/nextid)`  
`Name="HOMELAB-01-17.1.2"`  
`Qcow2="BIGIP-17.1.2-0.0.8.qcow2"`  
`#create VM `  
`qm create $NextID --machine pc-i440fx-2.12 --name $Name --memory 16384 --cores 4 --net0 vmxnet3,bridge=vmbr0,tag=100 --net1 vmxnet3,bridge=vmbr0,tag=200 --net2 vmxnet3,bridge=vmbr0,tag=300 --net3 vmxnet3,bridge=vmbr0,tag=220 --net4 vmxnet3,bridge=vmbr1`  
`#import qcow`  
`qm importdisk $NextID $Qcow2 local-lvm`  
`#attach disk to vm`  
`qm set $NextID --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-$NextID-disk-0`  
`#set as bootable`  
`qm set $NextID --boot c --bootdisk scsi0`  
`#start the VM`  
`qm start $NextID`

**2.** On the second Proxmox Server, run the following:

Proxmox04:

`#BIGIP02 on the second proxmox server`

`#Get Next VM ID and setup variables`  
`NextID=$(pvesh get /cluster/nextid)`  
`Name="HOMELAB-02-17.1.2"`  
`Qcow2="BIGIP-17.1.2-0.0.8.qcow2"`  
`#create VM`  
`qm create $NextID --machine pc-i440fx-2.12 --name $Name --memory 16384 --cores 4 --net0 vmxnet3,bridge=vmbr0,tag=100 --net1 vmxnet3,bridge=vmbr0,tag=200 --net2 vmxnet3,bridge=vmbr0,tag=300 --net3 vmxnet3,bridge=vmbr0,tag=220 --net4 vmxnet3,bridge=vmbr1`  
`#import qcow`  
`qm importdisk $NextID $Qcow2 local-lvm`  
`#attach disk to vm`  
`qm set $NextID --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-$NextID-disk-0`  
`#set as bootable`  
`qm set $NextID --boot c --bootdisk scsi0`  
`#start the VM`  
`qm start $NextID`

**3.** On the First BIG-IP's Console, run the following:

BIG-IP01 CONSOLE

`config`

**4.** On the second BIG-IP's Console, run the following:

BIG-IP 02 CONSOLE

`config`

**5.** On the First BIG-IP, via ssh, run the following:

BIGIP01 SSH

`tmsh modify sys global-settings gui-setup disabled`  
`passwd admin yourpasswordhere`  
`ls -l /var/config/rest/downloads`

**6.** On the second BIG-IP, via ssh, run the following:

BIGIP02 SSH

`tmsh modify sys global-settings gui-setup disabled`  
`passwd admin yourpasswordhere`  
`ls -l /var/config/rest/downloads`

**7.** On the Orchestration system, run the following:

`cd projects/HomeLab/F5/BIG-IP\ Classic/`  
`ls `

`# Upload DO`  
`FN=f5-declarative-onboarding-1.46.0-7.noarch.rpm`  
`#have it there already.`  
`#curl -o $FN `[`https://github.com/F5Networks/f5-declarative-onboarding/releases/download/v1.46.0/$FN`](https://github.com/F5Networks/f5-declarative-onboarding/releases/download/v1.46.0/$FN)  
`CREDS=admin:yourpasswordhere`  
`IP1=10.100.100.30`  
`IP2=10.100.100.31`  
`LEN=$(wc -c $FN | cut -f 1 -d ' ')`  
`curl -kvu $CREDS `[`https://$IP1/mgmt/shared/file-transfer/uploads/$FN`](https://$IP1/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`  
`curl -kvu $CREDS `[`https://$IP2/mgmt/shared/file-transfer/uploads/$FN`](https://$IP2/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`

`# Upload AS3`  
`FN=f5-appsvcs-3.53.0-7.noarch.rpm`  
`#have it there already.`  
`#curl -o $FN `[`https://github.com/F5Networks/f5-appsvcs-extension/releases/download/v3.53.0/$FN`](https://github.com/F5Networks/f5-appsvcs-extension/releases/download/v3.53.0/$FN)  
`CREDS=admin:yourpasswordhere`  
`IP1=10.100.100.30`  
`IP2=10.100.100.31`  
`LEN=$(wc -c $FN | cut -f 1 -d ' ')`  
`curl -kvu $CREDS `[`https://$IP1/mgmt/shared/file-transfer/uploads/$FN`](https://$IP1/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`  
`curl -kvu $CREDS `[`https://$IP2/mgmt/shared/file-transfer/uploads/$FN`](https://$IP2/mgmt/shared/file-transfer/uploads/$FN)` -H 'Content-Type: application/octet-stream' -H "Content-Range: 0-$((LEN - 1))/$LEN" -H "Content-Length: $LEN" -H 'Connection: keep-alive' --data-binary @$FN`

**8.** On the first BIG-IP, run the following:

BIGIP01 SSH

`ls -l /var/config/rest/downloads`

**9.** On the second BIG-IP, run the following:

BIGIP02 SSH

`ls -l /var/config/rest/downloads`

**10.** On the Orchestration system, run the following:

`#install DO`  
`FN=f5-declarative-onboarding-1.46.0-7.noarch.rpm DATA="{\"operation\":\"INSTALL\",\"packageFilePath\":\"/var/config/rest/downloads/$FN\"}"`  
`curl -kvu $CREDS "`[`https://$IP1/mgmt/shared/iapp/package-management-tasks`](https://$IP1/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP1`](https://$IP1)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`  
`curl -kvu $CREDS "`[`https://$IP2/mgmt/shared/iapp/package-management-tasks`](https://$IP2/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP2`](https://$IP2)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`

`#install AS3`  
`FN=f5-appsvcs-3.53.0-7.noarch.rpm`  
`DATA="{\"operation\":\"INSTALL\",\"packageFilePath\":\"/var/config/rest/downloads/$FN\"}"`  
`curl -kvu $CREDS "`[`https://$IP1/mgmt/shared/iapp/package-management-tasks`](https://$IP1/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP1`](https://$IP1)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`  
`curl -kvu $CREDS "`[`https://$IP2/mgmt/shared/iapp/package-management-tasks`](https://$IP2/mgmt/shared/iapp/package-management-tasks)`" -H "Origin: `[`https://$IP2`](https://$IP2)`" -H 'Content-Type: application/json;charset=UTF-8' --data $DATA`

(repeat as needed, till all are "FINISHED")

`QUERY="\{ \"operation\": \"QUERY\" \}"`  
`curl -k --location --request GET '`[`https://$IP1/mgmt/shared/iapp/package-management-tasks/`](https://$IP1/mgmt/shared/iapp/package-management-tasks/)`' --header 'Content-Type: application/json' --header 'Authorization: Basic YWRtaW46eW91cnBhc3N3b3JkaGVyZQ==' --header 'Cookie: BIGIPAuthCookie=C3obTQJ40rhalWvsv0t6H6ZKwzvw9Epa5IjcI5gj; BIGIPAuthUsernameCookie=admin' --data '{ "operation": "QUERY" }'|jq|grep -E 'packageName|status|id|error|startTime|endTime'`  
`curl -k --location --request GET '`[`https://$IP2/mgmt/shared/iapp/package-management-tasks/`](https://$IP2/mgmt/shared/iapp/package-management-tasks/)`' --header 'Content-Type: application/json' --header 'Authorization: Basic YWRtaW46eW91cnBhc3N3b3JkaGVyZQ==' --header 'Cookie: BIGIPAuthCookie=7CdqDA8BdqilADGBBCPQC7jcITngUL6DRWvwI5pe; BIGIPAuthUsernameCookie=admin' --data '{ "operation": "QUERY" }'|jq|grep -E 'packageName|status|id|error|startTime|endTime'`

`#send DO declaration for licensing, base config, etc`  
`ID1=$(curl -k -X POST -u admin:yourpasswordhere -H "Content-Type: application/json" -d @clustered01.json `[`https://$IP1/mgmt/shared/declarative-onboarding`](https://$IP1/mgmt/shared/declarative-onboarding)` | jq | grep '\"id\"' | awk '{print $2}'|sed 's/[\"|\,| ]//g');echo $ID1`  
`ID2=$(curl -k -X POST -u admin:yourpasswordhere -H "Content-Type: application/json" -d @clustered02.json `[`https://$IP2/mgmt/shared/declarative-onboarding`](https://$IP2/mgmt/shared/declarative-onboarding)` | jq | grep '\"id\"' | awk '{print $2}'|sed 's/[\"|\,| ]//g');echo $ID2`  
`echo "$ID1 --- $ID2"`

(repeat as needed until the status is completed)

`#Check the status of the declarations, this can take a few minutes, be patient and wait for both to register "success"`  
`curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1`](https://$IP1/mgmt/shared/declarative-onboarding/task/$ID1)` | jq | grep -A15 result && curl -k -X GET -u admin:yourpasswordhere -H "Content-Type: application/json" `[`https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2`](https://$IP2/mgmt/shared/declarative-onboarding/task/$ID2)` | jq | grep -A15 result`

`#deploy AS3 Configuration`  
`CREDS=admin:yourpasswordhere`  
`IP1=10.100.100.30`  
`IP2=10.100.100.31`  
`#Note: lab-as3.json is a defivitive of: `[`https://github.com/F5Networks/f5-appsvcs-extension/blob/main/examples/declarations/example-http-https-one-declaration.json`](https://github.com/F5Networks/f5-appsvcs-extension/blob/main/examples/declarations/example-http-https-one-declaration.json)  
`curl -k -X POST -u $CREDS -H "Content-Type: application/json" -d @lab-as3.json `[`https://$IP1/mgmt/shared/appsvcs/declare`](https://$IP1/mgmt/shared/appsvcs/declare)

`#perform a config sync`  
`curl -sk -u $CREDS -H "Content-Type: application/json" -X POST -d '{"command":"run","utilCmdArgs":"config-sync to-group failoverGroup"}' `[`https://$IP1/mgmt/tm/cm`](https://$IP1/mgmt/tm/cm)

