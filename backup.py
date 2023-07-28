#!/usr/bin/env python3

import time
import subprocess
import smtplib
import ssl
import random
from pathlib import Path
import os

#
# Backup config
#

# Subdirectories of home dir to backup.
BACKUP_DIRS: list[Path] = [
    Path.home() / "Documents",
    Path.home() / "Pictures",
    Path.home() / "Music",
    Path.home() / "Videos",
    Path.home() / "VirtualBox VMs",
    Path.home() / "iso",
    Path("C:\\Program Files (x86)\\Steam\\steamapps\\common"),
    Path("C:\\tools"),
]

RESTIC_CLOUD_CONFIG = {
    # Restic repository location.
    # Here we're using a B2 bucket with the S3-compatible endpoint as
    # recommended over the B2 backend in the documentation
    "RESTIC_REPOSITORY": "s3:s3.us-east-005.backblazeb2.com/mybucketname",
    # From B2 application key
    "AWS_ACCESS_KEY_ID": "XXXXXXXXXXXXXXXXXXXXXXXXX",
    "AWS_SECRET_ACCESS_KEY": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    # For Restic's encryption
    "RESTIC_PASSWORD": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
}

RESTIC_LOCAL_WINDOWS_CONFIG = {
    # Restic repository location.
    "RESTIC_REPOSITORY": "Z:\\restic",
    # For Restic's encryption
    "RESTIC_PASSWORD": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
}

RESTIC_LOCAL_WSL_CONFIG = {
    # Restic repository location.
    "RESTIC_REPOSITORY": "/mnt/z/restic",
    # For Restic's encryption
    "RESTIC_PASSWORD": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
}

# Send email on success/error
EMAIL_ADDRESS = "me@example.com"
EMAIL_PASSWORD = "XXXXXXXXXXXXXXXX"

# Don't back up paths that match these patterns
EXCLUDE_PATTERNS = [
    "node_modules/**",
    ".cache/**",
    ".vscode/**",
    ".npm/**",
    ".vscode-server/**",
]

#
# End backup config
#


def gen_exclude_flags(patterns: list[str]):
    flags = []
    for pattern in patterns:
        flags.append("--exclude")
        flags.append(pattern)
    return flags


# Restic flags common to Windows and WSL
RESTIC_DEFAULT_ARGS = gen_exclude_flags(EXCLUDE_PATTERNS)


class ShellError(Exception):
    def __init__(self, msg):
        self.msg = msg


def sh(cmd: list[str], check=True, stdin_str=None, env=None):
    print(f"Running command: {cmd}")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    stdout, stderr = proc.communicate(stdin_str)
    stdout = stdout.strip()
    stderr = stderr.strip()
    if check and proc.returncode != 0:
        msg = (
            f"[script] Failed to run command: {proc.args}\n"
            f"[script] Return code: {proc.returncode}\n\n"
            f"[script] Stdout:\n{stdout}\n"
            f"[script] Stderr:\n{stderr}"
        )
        print(msg)
        raise ShellError(msg)
    if len(stdout) > 0 or len(stderr) > 0:
        print(f"[script] Stdout:\n{stdout}\n[script] Stderr:\n{stderr}")
    return stdout


def notify(subject, msg):
    port = 465  # For SSL

    # Create a secure SSL context
    context = ssl.create_default_context()

    with smtplib.SMTP_SSL("smtp.gmail.com", port, context=context) as server:
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        message = f"Subject: {subject}\n\n{msg}"

        server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, message)


def try_task(task_func, error_list):
    try:
        task_func()
    except ShellError as e:
        error_list.append(e.msg)
    except Exception as e:
        error_list.append(e)


def backup_windows_dir(config, dir: Path):
    print(f"Snapshotting dir with restic: {dir}")
    cmd = (
        ["restic"]
        + RESTIC_DEFAULT_ARGS
        + ["backup", str(dir), "--use-fs-snapshot", "--tag", "Windows"]
    )
    env = os.environ.copy()
    for var, val in config.items():
        env[var] = val
    sh(cmd, env=env)
    print(f"Finished snapshotting dir with restic: {dir}")


def choco_upgrade():
    sh(["choco", "upgrade", "all"])
    print("Upgraded Chocolatey packages")


def wsl_upgrade():
    sh(["wsl.exe", "sudo", "apt", "update"])
    sh(["wsl.exe", "sudo", "apt", "upgrade", "-y"])
    print("Updated apt packages in WSL")


def restic_upgrade():
    sh(["restic", "self-update"])


def backup_c_drive(config, errors):
    shuffled_dirs = BACKUP_DIRS.copy()
    random.shuffle(shuffled_dirs)
    for backup_dir in shuffled_dirs:
        try_task(lambda: backup_windows_dir(config, backup_dir), errors)
    print("Backed up C drive")


def backup_wsl(config):
    # In case I forgot to kill `restic mount`, don't try to backup the mountpoint... ugh
    sh(["wsl.exe", "killall", "restic"], check=False)

    # Generate WSLENV, which tells WSL which Windows env vars to pass to WSL
    env = os.environ.copy()
    if "WSLENV" in env:
        wslenv = env["WSLENV"]
    else:
        wslenv = ""
    for var_name in config.keys():
        wslenv += f":{var_name}"
    env["WSLENV"] = wslenv

    # Add Restic environment vars to the custom Windows environment to run wsl.exe in
    for var, val in config.items():
        env[var] = val

    cmd = [
        "wsl.exe",
        "--shell-type",
        "none",  # We don't want bash/zsh to try expanding our exclude glob patterns
        "/home/alex/.local/bin/restic",
        "self-update",
    ]
    sh(cmd, env=env)

    cmd = [
        "wsl.exe",
        "--shell-type",
        "none",  # We don't want bash/zsh to try expanding our exclude glob patterns
        "/home/alex/.local/bin/restic",
        "backup",
        "/home/alex",
        "--tag",
        "WSL",
    ] + RESTIC_DEFAULT_ARGS
    sh(cmd, env=env)
    print("Backed up WSL")


def check_restic_integrity(config):
    # TODO once per week, do more complete but time-consuming integrity check
    env = os.environ.copy()
    for var, val in config.items():
        env[var] = val
    sh(["restic", "check"], env=env)


def backup_to_restic_repo(restic_win_config, restic_wsl_config, errors):
    try_task(lambda: backup_c_drive(restic_win_config, errors), errors)
    try_task(lambda: backup_wsl(restic_wsl_config), errors)
    try_task(lambda: check_restic_integrity(restic_win_config), errors)


def main():
    errors = []

    time.sleep(60)  # Wait for network to come online

    try_task(choco_upgrade, errors)
    try_task(wsl_upgrade, errors)
    try_task(restic_upgrade, errors)

    # Actually backup data
    backup_to_restic_repo(RESTIC_LOCAL_WINDOWS_CONFIG, RESTIC_LOCAL_WSL_CONFIG, errors)
    backup_to_restic_repo(RESTIC_CLOUD_CONFIG, RESTIC_CLOUD_CONFIG, errors)

    if len(errors) == 0:
        notify("Backup succeeded", "Hope you're having a nice day :)")
    else:
        subject = f'Backup failed! {len(errors)} error{"" if len(errors) == 1 else "s"}'
        msg = "".join(str(e) for e in errors).strip()
        notify(subject, msg)
    print("Reported backup errors")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify("Backup failed: 1 error", e)

