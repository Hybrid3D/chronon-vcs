# Installation

Chronon needs Python 3.11+ and runs on Windows 10/11, Linux, WSL2, and macOS.
Install it with [`uv`](https://docs.astral.sh/uv/) or
[`pipx`](https://pipx.pypa.io/latest/how-to/install-pipx.html), which give the
CLI its own environment and put `chronon` and `chronon-mcp` on `PATH`.

## From PyPI

```bash
uv tool install chronon-vcs      # or: pipx install chronon-vcs
uv tool upgrade chronon-vcs      # or: pipx upgrade chronon-vcs
```

If the commands are not found, run `uv tool update-shell` or `pipx ensurepath`
once and open a new terminal.

## From GitHub

Pin a released tag for a reproducible install:

```bash
uv tool install "git+https://github.com/Hybrid3D/chronon-vcs@v0.2.8"
# or
pipx install "git+https://github.com/Hybrid3D/chronon-vcs@v0.2.8"
```

Reinstall with a newer tag and `--force` to upgrade. Each
[release](https://github.com/Hybrid3D/chronon-vcs/releases) also ships a wheel for
offline installs:

```bash
pipx install ./chronon_vcs-0.2.8-py3-none-any.whl
```

## Platform notes

- **macOS:** `brew install python pipx && pipx ensurepath`
- **Linux:** `sudo apt install pipx` (Ubuntu 23.04+/Debian 12+) or
  `sudo dnf install pipx`, then `pipx ensurepath`. Do not use `sudo pip`.
- **WSL2:** follow the Linux steps inside WSL. Keep Chronon and the vault on
  the same side of the Windows/WSL boundary; prefer `~/...` over `/mnt/c/...`.
- **Windows (PowerShell):** install Python from
  [python.org](https://www.python.org/downloads/windows/), then:

  ```powershell
  py -m pip install --user pipx
  py -m pipx ensurepath
  # reopen PowerShell
  pipx install chronon-vcs
  ```

## From a local checkout

```bash
git clone https://github.com/Hybrid3D/chronon-vcs
cd chronon-vcs
pipx install --force .     # or: pip install -e ".[dev]" for development
```

## Uninstalling

```bash
uv tool uninstall chronon-vcs    # or: pipx uninstall chronon-vcs
```

This removes the commands only. Vault files, `.chronon/` history, and the vault
registry stay in place.
