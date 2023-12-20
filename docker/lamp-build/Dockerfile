FROM ubuntu

#Setup the entrypoint file
COPY mario.sh /safe-ish/mario.sh

#Setup permissions. This is probably too much permission...
RUN chmod 755 /safe-ish/mario.sh

#Install needed packages.
RUN apt-get -y update
RUN DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install apt-transport-https ca-certificates 
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install openssh-client openssh-server 
#insecure test
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install net-tools tcpdump iputils-ping nano curl apt-utils ufw
#lamp Install
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install apache2 mysql-server php libapache2-mod-php php-mysql
RUN DEBIAN_FRONTEND=noninteractive ufw app info "Apache Full"

#Upgrade packages, fixing missing.
RUN DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --fix-missing

EXPOSE 80 443

WORKDIR /root

ENTRYPOINT ["/safe-ish/mario.sh"]