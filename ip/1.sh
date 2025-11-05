#!/bin/bash
#
sudo nmcli con mod "Wired connection 1" ipv4.addresses 10.1.91.101/24
sudo nmcli con mod "Wired connection 1" ipv4.gateway 10.1.91.1
sudo nmcli con mod "Wired connection 1" ipv4.dns "10.1.91.1 8.8.8.8"
sudo nmcli con mod "Wired connection 1" ipv4.method manual
sudo nmcli con up "Wired connection 1"
