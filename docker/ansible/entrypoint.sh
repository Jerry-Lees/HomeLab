#! /bin/bash
echo Starting Container...
service cron start
service ssh stop
service ssh start
while true; do sleep 1; done;
