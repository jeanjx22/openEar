#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Pushing to GitHub ==="
git push origin main

echo "=== Updating EC2 ==="
ssh -i openear-key.pem ubuntu@18.145.194.240 'cd ~/openEar && git checkout -- . && git pull'

echo "=== Restarting bot ==="
ssh -t -i openear-key.pem ubuntu@18.145.194.240 'sudo systemctl restart openear'

echo "=== Done ==="
