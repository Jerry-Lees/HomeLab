#Set the base container to the official jenkins container
FROM jenkins/jenkins:lts-jdk11

USER root

#Setup certs in the container.
RUN mkdir -p /root/.ssh
COPY id_rsa /root/.ssh/id_rsa
COPY id_rsa.pub /root/.ssh/id_rsa.pub

#Setup the entrypoint file.
#the entry point file is in the jenkins base image as /usr/local/bin/jenkins.sh

#Setup permissions.
RUN chmod 600 /root/.ssh/id_rsa
RUN chmod 640 /root/.ssh/id_rsa.pub
RUN echo "Host *" > /root/.ssh/config && echo " StrictHostKeyChecking no" >> /root/.ssh/config

#Install needed packages.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get -y install dialog apt-utils 
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install openssh-client openssh-server ansible git cron net-tools tcpdump iputils-ping nano vim sudo

#Upgrade packages, fixing missing.
RUN DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --fix-missing

#-----------------------------------------------------
#Install docker
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install apt-transport-https \
    ca-certificates \
    curl \
    gnupg2 \
    software-properties-common && \
curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg > /tmp/dkey; apt-key add /tmp/dkey && \
add-apt-repository \
    "deb [arch=amd64] https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
    $(lsb_release -cs) \
    stable" && \
apt-get update && \
apt-get -y install docker-ce
RUN DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce
RUN usermod -a -G docker jenkins
USER root
#-----------------------------------------------------

EXPOSE 22 8080 50000

WORKDIR /root

ENTRYPOINT ["/usr/local/bin/jenkins.sh"]
