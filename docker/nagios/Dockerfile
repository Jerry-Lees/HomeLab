FROM ubuntu

#Setup the entrypoint file
COPY luigi.sh /safe-ish/luigi.sh

#Setup permissions. This is probably too much permission...
RUN chmod 755 /safe-ish/luigi.sh

RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections
RUN DEBIAN_FRONTEND=noninteractive 

#Install needed packages.
RUN apt-get -y update
RUN DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install apt-transport-https ca-certificates 
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install openssh-client openssh-server wget
RUN apt install -y \
	autoconf \
	gcc \
	libc6 \
	make \
	unzip \
	apache2 \
	apache2-utils \
	php \
	libapache2-mod-php \
	libgd-dev \
	libssl-dev \
	libmcrypt-dev \
	bc \
	gawk \
	dc \
	build-essential \
	snmp \
	libnet-snmp-perl \
	gettext \
	fping \
        iputils-ping \
	qstat \
	dnsutils \
	smbclient
#insecure test
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install net-tools tcpdump iputils-ping nano curl apt-utils ufw
#lamp Install
RUN DEBIAN_FRONTEND=noninteractive apt-get -y install apache2 mysql-server php libapache2-mod-php php-mysql
RUN DEBIAN_FRONTEND=noninteractive ufw app info "Apache Full"

#Upgrade packages, fixing missing.
RUN DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --fix-missing

#download Nagios and plugins
RUN mkdir /opt/nagios-core-docker
WORKDIR /opt/nagios-core-docker/
RUN wget https://github.com/NagiosEnterprises/nagioscore/releases/download/nagios-4.4.14/nagios-4.4.14.tar.gz
RUN tar xzf nagios-4.4.14.tar.gz
RUN wget https://github.com/nagios-plugins/nagios-plugins/releases/download/release-2.4.6/nagios-plugins-2.4.6.tar.gz
RUN tar xzf nagios-plugins-2.4.6.tar.gz
RUN wget https://github.com/NagiosEnterprises/nrpe/releases/download/nrpe-4.1.0/nrpe-4.1.0.tar.gz
RUN tar xzf nrpe-4.1.0.tar.gz

# Building Nagios Core
WORKDIR /opt/nagios-core-docker/nagios-4.4.14
#RUN cd nagios-4.4.14
RUN ./configure --with-httpd-conf=/etc/apache2/sites-enabled && \
    make all && \
    make install-groups-users && \
    usermod -aG nagios www-data && \
    make install && \
    make install-init && \
    make install-daemoninit && \
    make install-commandmode && \
    make install-config && \
    make install-webconf && \
    a2enmod rewrite cgi
# Building Nagios Plugins
WORKDIR /opt/nagios-core-docker/nagios-plugins-2.4.6
#RUN cd ../nagios-plugins-2.4.6
RUN pwd
RUN ls -l
RUN ./configure --with-nagios-user=nagios --with-nagios-group=nagios && \
    make && \
    make install
# Build and Install NRPE Plugins
WORKDIR /opt/nagios-core-docker/nrpe-4.1.0
#RUN cd ../nrpe-4.1.0
RUN ./configure && \
    make all && \
    make install-plugin
WORKDIR /root
# Copy the Nagios basic auth credentials set in the env file;
COPY .env /usr/local/nagios/etc/.env
# Add Nagios and Apache Startup script

EXPOSE 80 443

WORKDIR /root

ENTRYPOINT ["/safe-ish/luigi.sh"]