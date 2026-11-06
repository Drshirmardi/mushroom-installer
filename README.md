# Mushroom installer

Standalone server installer for Ubuntu 24.04 LTS (amd64).

Install or replace the old installer:

```bash
curl -fsSL https://raw.githubusercontent.com/Drshirmardi/mushroom-installer/main/install.sh | sudo bash
```

```bash
sudo mushroom --version
sudo mushroom update --check
sudo mushroom update
sudo mushroom update --version v1.0.12
```

Choose an initial version with `sudo bash -s -- --version v1.0.12` instead of `sudo bash`. Use `--no-run` to install the tool without opening setup/panel. Updates preserve existing server settings and secrets. Opening the panel checks for updates and asks before installing.

Operational details: [installation guide](installation/README.md).
