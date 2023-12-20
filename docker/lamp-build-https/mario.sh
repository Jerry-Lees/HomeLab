#! /bin/bash
echo Starting Container...
#service cron start
echo Starting Services...
service apache2 start
service mysql start
service ssh start
service webmin start
service apache2 start
while true; do sleep 1; done;
