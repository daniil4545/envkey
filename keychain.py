"""Доступ к login keychain через `security`. Пароли без запроса не читаются."""

import re
import subprocess

ACCT = re.compile(r'^\s+"acct"<blob>="(.*)"$')
SVCE = re.compile(r'^\s+"svce"<blob>="(.*)"$')


class KeychainError(Exception):
    pass


def list_items():
    """Пары (service, account) из login keychain. Пароли при этом не читаются."""
    dump = subprocess.run(
        ["security", "dump-keychain"], capture_output=True, text=True
    ).stdout
    items, acct = [], None
    for line in dump.splitlines():
        matched = ACCT.match(line)
        if matched:
            acct = matched.group(1)
            continue
        matched = SVCE.match(line)
        if matched and acct is not None:
            items.append((matched.group(1), acct))
            acct = None
    return items


def read_secret(service, account):
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise KeychainError(f"не удалось прочитать {service} -> {account}")
    return result.stdout.rstrip("\n")


def add_secret(service, account):
    """Завести или обновить запись. Значение спрашивает сам security: в argv его нет."""
    return subprocess.run(
        ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w"]
    ).returncode
