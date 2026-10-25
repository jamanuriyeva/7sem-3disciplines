#!/usr/bin/env bash
# Создаёт изолированную среду PyTorch/XPU для Intel Arc.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv_dir="$script_dir/.venv-xpu"

if [[ ! -x "$venv_dir/bin/python" ]]; then
  uv venv --python python3.13 "$venv_dir"
fi
uv pip install --index-strategy unsafe-best-match --python "$venv_dir/bin/python" -r "$script_dir/requirements-pytorch-xpu.txt"

"$venv_dir/bin/python" - <<'PY'
import torch

print(f'PyTorch: {torch.__version__}')
print(f'Intel XPU available: {torch.xpu.is_available()}')
if torch.xpu.is_available():
    device = torch.device('xpu')
    value = (torch.ones(8, device=device) * 2).sum().item()
    print(f'XPU smoke test: {value}')
else:
    print('XPU не обнаружен: PyTorch-ноутбук автоматически применит CPU.')
PY
