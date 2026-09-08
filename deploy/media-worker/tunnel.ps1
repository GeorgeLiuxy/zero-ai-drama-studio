$ErrorActionPreference='Stop'
# Interactive SSH authentication; no password is persisted by this script.
ssh -N -p 37678 -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:19000:127.0.0.1:9000 root@connect.westd.seetacloud.com
