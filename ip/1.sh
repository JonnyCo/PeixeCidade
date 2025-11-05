#!/bin/bash
CON_NAME="NF_VCAM"
IP="10.1.91.101/24"
GW="10.1.91.1"
DNS="10.1.91.1 8.8.8.8"

sudo nmcli con mod "$CON_NAME" ipv4.addresses "$IP"
sudo nmcli con mod "$CON_NAME" ipv4.gateway "$GW"
sudo nmcli con mod "$CON_NAME" ipv4.dns "$DNS"
sudo nmcli con mod "$CON_NAME" ipv4.method manual
sudo nmcli con up "$CON_NAME"
