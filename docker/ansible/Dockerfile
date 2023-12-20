FROM ubuntu:jammy
ENV TZ="America/Chicago"

#Setup certs in the container.
RUN mkdir -p /root/.ssh
COPY id_rsa /root/.ssh/id_rsa
COPY id_rsa.pub /root/.ssh/id_rsa.pub

#Setup the entrypoint file
COPY entrypoint.sh /root/entrypoint.sh

#Setup permissions.
RUN chmod 600 /root/.ssh/id_rsa \
 && chmod 640 /root/.ssh/id_rsa.pub \
 && chmod 777 /root/entrypoint.sh \
 && echo "Host *" > /root/.ssh/config && echo " StrictHostKeyChecking no" >> /root/.ssh/config

#Install needed packages.
RUN apt-get -y update
RUN DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install curl gnupg2 apt-utils openssh-client openssh-server ansible git cron net-tools tcpdump iputils-ping nano sudo pip ntp
RUN pip install paramiko
#setup NTP
RUN echo "server pool.ntp.org" >> /etc/ntp.conf
RUN echo "server time.nist.gov" >> /etc/ntp.conf
RUN service ntp start
RUN service ntp restart
RUN service ssh start
RUN service ssh restart

#Upgrade packages, fixing missing.
RUN apt-get upgrade -y --fix-missing


EXPOSE 22

WORKDIR /root

ENTRYPOINT ["/root/entrypoint.sh"]