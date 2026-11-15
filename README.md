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

For Bale Mini App authentication on the shared **Dev** server, install the Bale-capable
installer release and run:
```bash
sudo mushroom setup-bale --environment dev
# After synchronizing the Mushroom dev application:
sudo mushroom register-bale-webhook --environment dev
```
The first command saves the bot token privately; the second checks that the bot does
not already belong to another webhook consumer. Configure the main Mini App URL in
Bale BotFather to `https://<dev-app-domain>/bale`.

Operational details: [installation guide](installation/README.md).
