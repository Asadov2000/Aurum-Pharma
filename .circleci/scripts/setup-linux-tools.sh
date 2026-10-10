#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
sudo apt-get update -qq
sudo apt-get install -y python3.12 python3.12-venv
python3.12 -m venv .circleci-tools/python
export PATH="$PWD/.circleci-tools/python/bin:$PATH"
printf 'export PATH="%s/.circleci-tools/python/bin:$PATH"\n' "$PWD" >> "$BASH_ENV"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
test -s "$NVM_DIR/nvm.sh"
source "$NVM_DIR/nvm.sh"
nvm install 24
nvm use 24
npm install --global pnpm@9.12.0
printf 'export NVM_DIR="%s"\nsource "$NVM_DIR/nvm.sh"\nnvm use 24 >/dev/null\n' "$NVM_DIR" >> "$BASH_ENV"
if ! command -v pwsh >/dev/null; then
  curl --fail --silent --show-error --location https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb -o .circleci-tools/packages-microsoft-prod.deb
  sudo dpkg -i .circleci-tools/packages-microsoft-prod.deb
  sudo apt-get update -qq
  sudo apt-get install -y powershell
fi
mkdir -p .circleci-tmp
printf 'export RUNNER_TEMP="%s/.circleci-tmp"\n' "$PWD" >> "$BASH_ENV"
python --version
node --version
pnpm --version
pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
