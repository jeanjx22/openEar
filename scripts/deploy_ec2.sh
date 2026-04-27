#!/bin/bash
set -e

EC2_HOST="18.145.194.240"
KEY="openear-key.pem"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@$EC2_HOST"
SCP="scp -i $KEY -o StrictHostKeyChecking=no"

echo "=== Step 1: Install Docker + Python on EC2 ==="
$SSH << 'REMOTE'
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 python3.12 python3.12-venv python3-pip git
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ubuntu
echo "Docker installed"
REMOTE

echo ""
echo "=== Step 2: Clone repo ==="
$SSH << 'REMOTE'
if [ -d ~/openEar ]; then
    cd ~/openEar && git pull
else
    git clone https://github.com/jeanjx22/openEar.git ~/openEar
fi
echo "Repo ready"
REMOTE

echo ""
echo "=== Step 3: Upload config files ==="
$SCP .env ubuntu@$EC2_HOST:~/openEar/.env
$SCP credentials.json ubuntu@$EC2_HOST:~/openEar/credentials.json
$SCP token.json ubuntu@$EC2_HOST:~/openEar/token.json
echo "Config uploaded"

echo ""
echo "=== Step 4: Setup venv + install deps ==="
$SSH << 'REMOTE'
cd ~/openEar
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo "Dependencies installed"
REMOTE

echo ""
echo "=== Step 5: Create systemd service ==="
$SSH << 'REMOTE'
sudo tee /etc/systemd/system/openear.service > /dev/null << 'EOF'
[Unit]
Description=openEar Personal Assistant
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/openEar
ExecStart=/home/ubuntu/openEar/venv/bin/python -m src.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openear
sudo systemctl start openear
echo "Service started"
REMOTE

echo ""
echo "=== Step 6: Verify ==="
sleep 5
$SSH "sudo systemctl status openear --no-pager | head -15"

echo ""
echo "=== DONE ==="
echo "openEar is running 24/7 on EC2: $EC2_HOST"
echo "View logs: ssh -i $KEY ubuntu@$EC2_HOST 'sudo journalctl -u openear -f'"
