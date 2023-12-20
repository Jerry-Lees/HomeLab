FROM ubuntu:jammy

ENV DEBIAN_FRONTEND=noninteractive

#Setup the entrypoint file
COPY mario.sh /safe-ish/mario.sh
COPY www /var/www/html
COPY setup-repos.sh /safe-ish/setup-repos.sh

#This below assumes you have modified your apache config to add the *:443 virtual server using the certs (I have)
COPY domain.crt /certs/domain.crt
COPY domain.key /certs/domain.key

#Setup permissions. This is probably too much permission...
RUN chmod 755 /safe-ish/mario.sh
RUN chmod 755 /safe-ish/setup-repos.sh

#Install needed packages.
RUN apt-get -y update
RUN apt-get -y upgrade
RUN apt-get -y install apt-transport-https ca-certificates 
RUN apt-get -y install openssh-client openssh-server 
#RUN apt-get -y install curl
#Setup and install the webmin repo
#RUN curl -o setup-repos.sh https://raw.githubusercontent.com/webmin/webmin/master/setup-repos.sh
RUN sh /safe-ish/setup-repos.sh --force

RUN apt-get -y install webmin --install-recommends
#insecure test
RUN apt-get -y install net-tools tcpdump iputils-ping nano curl apt-utils ufw
#lamp Install
RUN apt-get -y install apache2 mysql-server php libapache2-mod-php php-mysql
RUN ufw app info "Apache Full"

#Upgrade packages, fixing missing.
RUN apt-get upgrade -y --fix-missing

EXPOSE 80 443

WORKDIR /root

ENTRYPOINT ["/safe-ish/mario.sh"]