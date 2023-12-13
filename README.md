# HomeLab
A set of random scripts and files used to build and maintain my Home Lab 

# Background
Over time my Home Lab has grown and evolved from just one server to more servers and unmanaged switches to even more servers/containers/clusters/NAS/Managed Switches/etc.
Likewise, my management of it has had to evolve to include Ansible, Terraform, k8s, and other things. 

Also, over time there have been the inevitable "mini" disasters, both failures of equipment and failures between the keyboard and the admin. All part of the territory, and I devleloped many of these files as ways to recover from them more quickly.

This repository is intended to be "everything I need" to recover systems (not data) to get back up and running, where ever possible. The bonus is that it might help someone else implement a similar setup or solve a problem they are (or didn't realize they were) having.

# My Setup
This section should detail things that are "odd" about my setup. Maybe they are better, maybe not-- but they are all concious decisions made for a valid reason at one point or another along the way. We all have them.

## Kubernetes
First, ingress... long ago I didn't know as much about k8s as I do now and likely still not nearly enough. Ingress is a PITA for k8s. MetalLB solves that for me just fine for a Home Lab. Is it production grade... No. Are you likely to be using it at "the office".. I doubt it, but anything's possible. Is it good enough and simple enough, yes. I took the easy path here and really haven't looked back or regretted it. All the Services in my Kubernetes Yamls will reference a pool of IPs that are setup in k8s as well as a IP address the service is listening on on my network. That service is then accessible (and it ARPs) by all my clients on the network... and even the Internet at large if I setup a PAT on the firewall.

Second, Storage... Storage classes for some reason escaped me and I struggled to get them up and running years ago. It was me, it was my lack of willingness to dig it--- so I did it the way I knew how... with a mounted Block device (LUN) via iSCSI. My storage that containes the files that need to be persistent when a pod dies or becomes unhealth is a mounted iSCSI drive on a NAS that is formatted for OCFS2 and all my nodes are a part of that cluster.

It's important to recognize that the nodes are a part of a OCFS2 Cluster--- that way they don't clobber each other's writes as they would with a less than cluster aware file system. This is probably harder than it needs to be, but then again-- it works and well.... choices were made. ;-)


# Some notes on format
Obviously there is sensitive data in some of these files. That data will be masked for the repository and will have to be corrected before the files are usable again
The following sections give you some assistance in recognizing these points in the files in the repository and will be updated as more masking needs to be done.

The below sections detail the things you will have to modify for certain.

## Fix IPs
Obviously, Internal IPs need to be masked. I will replace the first three octets of my internal addresses with "A.B.C.", "A.B.D.", etc. A sed statement with your ranges should be all that's needed to correct this... and of course make sure you aren't generating duplicates on your network.

 sed -i "s/A.B.C./[your subnet here]/g" filename.yaml
  or
 sed -i "s/A.B.D./[your subnet here]/g" filename.yaml

## Password masking
Clearly passwords need to go from the files to be sharable. No... P@ssw0rd, P@ssw0rd01, and Password01 are *NOT* my passwords. 

 sed -i "s/P@ssw0rd/[your password here]/g" filename.yaml
  or 
 sed -i "s/P@ssw0rd01/[your password here]/g" filename.yaml
  or 
 sed -i "s/Password01/[your password here]/g" filename.yaml

## Path masking
Paths should also me masked, so the following will assist you in that effort. (Though, you could also do away with the mechanism I mention above and NOT use an iSCSI disk like I am and manage your own storage methods. That is an excercise for the reader.)

 sed -i "s/\/sharedpath\/storagelocation\//\/mount-point-here\/path-here\//g" filename.yaml

## Domain/FQDN masking
domainnames needed to be masked as well. To undo this, run the following:
sed -i "s/example.com/your-domain.tld/g" filename.yaml

 
## Tricks/Lessons learned

One item you will see throughout the kubernetes yaml files in this repository is:

      - name: timezonefile
        hostPath:
            path: /etc/timezone
            type: File
      - name: localtimefile
        hostPath:
            path: /etc/localtime
            type: File

This is there becasue it forces the container to the same timezone and local time as the host it is running on. I looked at many different ways to "sync" time on my containers running in my cluster-- this seemed to be teh most fool proof and elegant.
			