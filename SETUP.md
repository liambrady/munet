Frr and Munet set up instructions:

** Setup environment **
python3 -m venv venv
source venv/bin/activate   # modify your ~/.profile to do this too
pip install pyang

** Prereqs **
sudo apt update -y
sudo apt-get install -y git autoconf automake libtool make libreadline-dev texinfo pkg-config libpam0g-dev libjson-c-dev bison flex libc-ares-dev python3-dev python3-sphinx install-info build-essential libsnmp-dev perl libcap-dev python2 libelf-dev libunwind-dev tmux
sudo groupadd -r frr sudo groupadd -r frrvty
sudo adduser --system --ingroup frr --home /var/run/frr/ --gecos "FRR suite" --shell /sbin/nologin frr
sudo usermod -a -G frrvty frr
sudo mkdir /etc/frr /var/run/frr /var/log/frr
sudo chown frr:frr /etc/frr /var/run/frr /var/log/frr

** Build FRR **
cd frr
autoreconf -i
mkdir build
cd build
../configure --prefix=/usr --localstatedir=/var/run/frr --sbindir=/usr/lib/frr --sysconfdir=/etc/frr --enable-vty-group=frrvty --enable-dev-build
make -j$(nproc)

** Install FRR **
cd frr/build
sudo make install

** Configure Simulations **

** Run Simulation **
sudo munet

