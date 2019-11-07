#!/usr/bin/env python

# Copyright (c) 2017-2019 Dell Inc. or its subsidiaries.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import shutil
import subprocess
import sys


def parse_arguments():
    """ Parses the input argments
    """

    parser = argparse.ArgumentParser(
        description="Deploys the Redfish VM.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("cfg_filename",
                        help="Redfish configuration file",
                        metavar="CFG-FILE")
    parser.add_argument("linux_iso",
                        help="Linux ISO file",
                        metavar="Linux-ISO")

    return parser.parse_args()


def create_kickstart(ks_filename, cfg_filename):
    """ Creates the kickstart file for the Redfish VM

    The kickstart file consists of 3 parts, two of which are fixed text. The
    middle section (part 2) is dynamically generated using the contents of the
    specified configuration file.

    NOTE:

    The fixed text (ks_part_1 and ks_part_3 variables) has '\' characters at
    the end of some lines. Sometimes there is one of them and sometimes there
    are two, and the distinction is important.

    - When a line ends in a single '\', Python will merge that line and the one
      that follows into a single long line of text. No '\' will appear at the
      end of the line in the kickstart file.

    - When a line ends in a double "\\", the back-slash is escaped in Python,
      and a single '\' will be appended to the line in the kickstart file.
    """

    ks_part_1 = """
install
text
cdrom
reboot

# Partitioning
ignoredisk --only-use=vda
zerombr
bootloader --boot-drive=vda

clearpart --all

part /boot --fstype=ext4 --size=500
part pv.01 --size=8192 --grow

volgroup VolGroup --pesize=4096 pv.01

logvol / --fstype=ext4 --name=lv_root --vgname=VolGroup --grow --size=1024
logvol swap --name=lv_swap --vgname=VolGroup --size=1024

keyboard --vckeymap=us --xlayouts='us'
lang en_US.UTF-8

auth --enableshadow --passalgo=sha512

%include /tmp/ks_include.txt

skipx
firstboot --disable
eula --agreed

%packages --ignoremissing
@core
@standard
@development-tools
-firewalld
iptables
net-tools
zip
unzip 
tcpdump
git
%end

%pre --log /tmp/redfish-pre.log

"""

    ks_part_3 = """
%end

%post --nochroot --logfile /root/redfish-post.log
# Copy the files created during the %pre section to /root of the
# installed system for later use.
  cp -v /tmp/redfish-pre.log /mnt/sysimage/root
  cp -v /tmp/ks_include.txt /mnt/sysimage/root
  cp -v /tmp/ks_post_include.txt /mnt/sysimage/root
  mkdir -p /mnt/sysimage/root/redfish-ks-logs
  cp -v /tmp/redfish-pre.log /mnt/sysimage/root/redfish-ks-logs
  cp -v /tmp/ks_include.txt /mnt/sysimage/root/redfish-ks-logs
  cp -v /tmp/ks_post_include.txt /mnt/sysimage/root/redfish-ks-logs
%end


%post

exec < /dev/tty8 > /dev/tty8
chvt 8

(
  # Source the variables from the %pre section
  . /root/ks_post_include.txt

  # Configure name resolution
  for ns in ${NameServers//,/ }
  do
    echo "nameserver ${ns}" >> /etc/resolv.conf
  done

  echo "GATEWAY=${Gateway}" >> /etc/sysconfig/network
  echo "MTU=${ens3_mtu}" >> /etc/sysconfig/network-scripts/ifcfg-ens3
  echo "MTU=${ens4_mtu}" >> /etc/sysconfig/network-scripts/ifcfg-ens4

  sed -i -e '/^DNS/d' -e '/^GATEWAY/d' \
/etc/sysconfig/network-scripts/ifcfg-ens3
  sed -i -e '/^DNS/d' -e '/^GATEWAY/d' \
/etc/sysconfig/network-scripts/ifcfg-ens4

  echo "$( ip addr show dev ens3 | awk '/inet / { print $2 }' | \
sed 's/\/.*//' )  ${HostName}" >> /etc/hosts

  cat <<EOIP > /etc/sysconfig/iptables
*filter
:INPUT ACCEPT [0:0]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
-A INPUT -m state --state RELATED,ESTABLISHED -j ACCEPT
-A INPUT -p icmp -j ACCEPT
-A INPUT -i lo -j ACCEPT
-A INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 80 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 443 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 2003 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 2004 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 3000 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 7002 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 4505 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 4506 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 6789 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 8002 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 8080 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 8181 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 10080 -j ACCEPT
-A INPUT -m state --state NEW -m tcp -p tcp --dport 10443 -j ACCEPT
COMMIT
EOIP

  # Add redfish-admin for remote/manual installation steps of integration
  /usr/sbin/groupadd -g 1000 redfish-admin
  /usr/sbin/useradd -d /home/redfish-admin -s /bin/bash -u 1000 -g 1000 redfish-admin
  # Add nginxsite user 
  /usr/sbin/groupadd -g 1001 nginxsite
  /usr/sbin/useradd -d /home/nginxsite -s /bin/bash -u 1001 -g 1001 rnginxsite

  # Add redfish-admin to sudoers
  echo "redfish-admin ALL = (root) NOPASSWD:ALL" > /etc/sudoers.d/redfish-admin
  echo "Defaults:redfish-admin !requiretty" >> /etc/sudoers.d/redfish-admin

  /usr/bin/yum -y update
  /usr/bin/yum install -y git python3-flask python3-requests nginx virt-install libvirt-daemon 
  /usr/bin/yum install -y virt-manager libvirt libvirt-python libvirt-client

) 2>&1 | /usr/bin/tee -a /root/redfish-post.log

chvt 6

%end
"""
    with open(ks_filename, "w") as ks:
        # Write part 1
        ks.write(ks_part_1)

        # Part 2 is dynamically created from the configuration file

        cfg_lines = []
        with open(cfg_filename, "r") as cfg_file:
            cfg_lines = [line.strip("\n") for line in cfg_file.readlines()]

        ks_include = "/tmp/ks_include.txt"
        ks_post_include = "/tmp/ks_post_include.txt"
        hostname = ""
        nameservers = ""
        gateway = ""

        for line in cfg_lines:
            if line.startswith("#") or line.startswith(";") or len(line) == 0:
                continue
            tokens = line.split()

            if tokens[0] == "rootpassword":
                ks.write("echo rootpw '{}' >> {}\n".
                         format(tokens[1], ks_include))

            elif tokens[0] == "timezone":
                ks.write("echo timezone '{}' --utc >> {}\n".
                         format(tokens[1], ks_include))

            elif tokens[0] == "hostname":
                hostname = tokens[1]
                ks.write("echo HostName='{}' >> {}\n".
                         format(hostname, ks_post_include))

            elif tokens[0] == "nameserver":
                nameservers = tokens[1]
                ks.write("echo NameServers='{}' >> {}\n".
                         format(nameservers, ks_post_include))

            elif tokens[0] == "gateway":
                gateway = tokens[1]
                ks.write("echo Gateway='{}' >> {}\n".
                         format(gateway, ks_post_include))

            elif tokens[0] == "ntpserver":
                ks.write("echo NTPServers='{}' >> {}\n".
                         format(tokens[1], ks_post_include))

            elif tokens[0] == "ens3":
                ks.write("echo network --activate --onboot=true --noipv6"
                         " --device='{}' --bootproto=static --ip='{}'"
                         " --netmask='{}' --hostname='{}'"
                         " --gateway='{}' --nameserver='{}' --mtu='{}'>> {}\n".
                         format(
                             tokens[0],
                             tokens[1],
                             tokens[2],
                             hostname,
                             gateway,
                             nameservers,
                             tokens[3],
                             ks_include))
                ks.write("echo ens3_mtu='{}' >> {}\n".
                         format(tokens[3], ks_post_include))

            elif tokens[0] == "ens4":
                ks.write("echo network --activate --onboot=true --noipv6"
                         " --device='{}' --bootproto=static --ip='{}'"
                         " --netmask='{}' --gateway='{}' --nodefroute "
                         "--mtu='{}' >> {}\n".
                         format(
                             tokens[0],
                             tokens[1],
                             tokens[2],
                             gateway,
                             tokens[3],
                             ks_include))
                ks.write("echo ens4_mtu='{}' >> {}\n".
                         format(tokens[3], ks_post_include))

        # Write part 3, and we're done
        ks.write(ks_part_3)


def main():
    args = parse_arguments()

    ks_filename = "redfish.ks"
    ks_tmp_filename = os.path.join("/tmp", ks_filename)
    create_kickstart(ks_tmp_filename, args.cfg_filename)

    images_path = "/store/data/images"
    redfish_image = os.path.join(images_path, "redfish.img")

    # Ensure the images directory exists
    try:
        # This may fail, including when the directory already exists
        os.makedirs(images_path)
    except:
        pass
    finally:
        # Final check for whether the images directory is valid. We don't
        # care about the contents, but rely on an exception if the directory
        # doesn't exist. If no exception then we're good.
        os.listdir(images_path)

    if os.path.exists(redfish_image):
        os.remove(redfish_image)

    virt_install_args = [
        "virt-install",
        "--name redfish",
        "--memory 16384",
        "--vcpus 4",
        "--hvm",
        "--os-type linux",
        "--disk {},bus=virtio,size=100".format(redfish_image),
        "--network bridge=br-pub-api",
        "--initrd-inject {}".format(ks_tmp_filename),
        "--extra-args 'ks=file:/{}'".format(ks_filename),
        "--noautoconsole",
        "--graphics spice",
        "--autostart",
        "--location {}".format(args.linux_iso)
    ]

    return subprocess.call(" ".join(virt_install_args), shell=True)


if __name__ == "__main__":
    sys.exit(main())
