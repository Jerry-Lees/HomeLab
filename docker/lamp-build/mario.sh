#! /bin/bash
echo Starting Container...
#service cron start
echo Starting Services...
service apache2 start
service mysql start
service stop ssh
service start ssh
while true; do sleep 1; done;
